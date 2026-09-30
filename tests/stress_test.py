"""Stress test for bg3-data-mcp: drives the real server over stdio with the official MCP client.

Run:  UV_PROJECT_ENVIRONMENT=~/.cache/bg3-data-mcp/venv uv run python tests/stress_test.py
Writes tests/STRESS_REPORT.md.
"""
import asyncio
import glob
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from bg3data import query, sources  # noqa: E402

ENV = dict(os.environ)
ENV["UV_PROJECT_ENVIRONMENT"] = os.path.expanduser("~/.cache/bg3-data-mcp/venv")
SERVER = StdioServerParameters(command="uv", args=["run", "--quiet", "--directory", REPO, "bg3-data-mcp"], env=ENV)

report, failures = [], []


def rec(section, ok, detail):
    report.append((section, ok, detail))
    if not ok:
        failures.append(f"{section}: {detail}")
    print(("PASS " if ok else "FAIL ") + f"[{section}] {detail}", flush=True)


def text_of(result):
    return "\n".join(getattr(c, "text", "") for c in (result.content or []))


async def call(session, tool, args=None, timeout=300):
    t0 = time.perf_counter()
    r = await session.call_tool(tool, args or {}, read_timeout_seconds=timeout)
    return text_of(r), bool(getattr(r, "is_error", False) or getattr(r, "isError", False)), time.perf_counter() - t0


GOLDEN = [
    # (tool, args, must-contain list, description)
    ("bg3_get_entry", {"name": "Shout_HealingWord_Mass_7"}, ["6d4", "[apotheosis]", "@ dnd55e"], "Apotheosis upcast chains via dnd55e to base"),
    ("bg3_get_entry", {"name": "Shout_HealingWord_Mass_6"}, ["4d4", "[base/SharedDev]"], "dnd55e gap: vanilla 6th tier still 4d4"),
    ("bg3_get_entry", {"name": "Target_ChillTouch", "layers": []}, ["RangedSpellAttack"], "base only: vanilla Chill Touch is ranged"),
    ("bg3_get_entry", {"name": "Target_ChillTouch", "layers": ["dnd55e"]}, ["MeleeSpellAttack", "[dnd55e]"], "+dnd55e: melee (2024)"),
    ("bg3_diff", {"name": "Target_ChillTouch", "layer": "dnd55e"}, ["SpellRoll", "MeleeSpellAttack"], "diff shows dnd55e change"),
    ("bg3_progression", {"key": "Soulknife"}, ["PassivesRemoved=Soulknife_9_PsychicVeil", "L13", "L17"], "Psychic Veil moved 9->13"),
    ("bg3_progression", {"key": "Soulknife", "layers": ["dnd55e"]}, ["Soulknife_9_PsychicVeil"], "without apotheosis: no removal"),
    ("bg3_template", {"key": "f5643770-a043-428d-bfc9-02bdbc69e53f"}, ["Poltergeist", "BASE_Nightmare_Nurse", "GHOST_FX"], "template chain"),
    ("bg3_effect", {"guid": "4cab2089-4c14-44f6-9fe0-5421ec911552"}, ["VampiricTouch_CastEffect", "Dummy_R_HandFX", "Target_VampiricTouch"], "MEI resolve + users"),
    ("bg3_search_effects", {"text": "necrotic beam"}, ["WoundingRay"], "effect search, words any order"),
    ("bg3_spell_visuals", {"name": "Target_VampiricTouch"}, ["VampiricTouch_CastEffect", "VFX_Spells_Cast"], "visual kit names effects"),
    ("bg3_loca", {"query": "h1a950eaag6cb3g9b5cgfb98gc721a9abd164"}, ["Red Cap", "dnd55e"], "loca handle"),
    ("bg3_search", {"text": "SummonUndead"}, [], "search runs (may be empty upstream)"),
    ("bg3_references", {"token": "Paladin_Aura30ft"}, ["Paladin", "apotheosis"], "references include progression"),
    ("bg3_spell_list", {"key": "04e48c5c-d77b-44e7-9043-536293e2e18d"}, ["Wizard Savant Necromancy"], "spell list by UUID"),
    ("bg3_similar_spells", {"damage_type": "Necrotic", "level": 3, "limit": 5}, ["Necro"], "similar spells"),
    ("bg3_layers", {}, ["base", "dnd55e", "apotheosis", "fx"], "layers + timestamps"),
]

FUZZ = [
    ("bg3_get_entry", {"name": "Target_Heal", "layers": ["nope"]}, "error", "unknown layer"),
    ("bg3_get_entry", {"name": ""}, "not found", "empty name"),
    ("bg3_get_entry", {"name": "'; DROP TABLE stats; --"}, "not found", "SQL injection name"),
    ("bg3_search", {"text": ""}, "error", "empty search"),
    ("bg3_search", {"text": "%"}, None, "literal percent"),
    ("bg3_search", {"text": "_"}, None, "literal underscore"),
    ("bg3_search", {"text": "Ωmega 🔥 ünïcode"}, "no matches", "unicode"),
    ("bg3_search", {"text": "x" * 100_000}, "maximum is 500", "100k-char input rejected cleanly"),
    ("bg3_search", {"text": "Heal", "limit": -5}, None, "negative limit clamps"),
    ("bg3_search", {"text": "a", "limit": 10 ** 9}, "@lines<=500", "huge limit is clamped to 500 results"),
    ("bg3_references", {"token": "ab"}, "error", "too-short token"),
    ("bg3_effect", {"guid": "not-a-guid"}, "error", "bad GUID"),
    ("bg3_effect", {"guid": "00000000-0000-0000-0000-000000000000"}, "no MultiEffectInfo", "unknown GUID"),
    ("bg3_search_effects", {"text": "a"}, "error", "too-short effect search"),
    ("bg3_progression", {"key": "Soulknife", "level": 999}, "no progression", "impossible level"),
    ("bg3_diff", {"name": "Target_Heal", "layer": "nope"}, "not in the active stack", "diff unknown layer"),
    ("bg3_loca", {"query": "h" + "z" * 30}, "not found", "bad handle"),
    ("bg3_template", {"key": "../../etc/passwd"}, "not found", "path-like key"),
    ("bg3_add_mod_layer", {"name": "bogus", "path": "/definitely/not/here"}, "error", "bad layer path"),
    ("bg3_remove_mod_layer", {"name": "nope"}, "no mod layer", "remove unknown"),
]


def rss_mb():
    try:
        pids = subprocess.check_output(["pgrep", "-f", "bg3data.server|bin/bg3-data-mcp"], text=True).split()
        best = 0
        for pid in pids:
            for line in open(f"/proc/{pid}/status"):
                if line.startswith("VmRSS"):
                    best = max(best, int(line.split()[1]) // 1024)
        return best
    except Exception:
        return None


async def mcp_tests():
    async with stdio_client(SERVER) as (r, w):
        async with ClientSession(r, w) as session:
            t0 = time.perf_counter()
            await session.initialize()
            tools = (await session.list_tools()).tools
            names = sorted(t.name for t in tools)
            rec("protocol", len(names) == 21, f"{len(names)} tools: {', '.join(names)}")
            out, err, dt = await call(session, "bg3_layers")
            rec("protocol", not err and "base" in out, f"first call (cold start incl. index check) {dt:.1f}s; startup {time.perf_counter() - t0:.1f}s")

            # 2. golden answers
            for tool, args, must, desc in GOLDEN:
                out, err, dt = await call(session, tool, args)
                missing = [m for m in must if m not in out]
                rec("golden", not err and not missing and "internal error" not in out,
                    f"{desc} ({tool}, {dt*1000:.0f} ms)" + (f" MISSING {missing}; got: {out[:200]!r}" if missing or err else ""))

            # 3. fuzz
            before = sqlite_counts()
            for tool, args, expect, desc in FUZZ:
                try:
                    out, err, dt = await call(session, tool, args)
                    if expect and expect.startswith("@lines<="):
                        ok = "internal error" not in out and len(out.splitlines()) <= int(expect[8:])
                    else:
                        ok = "internal error" not in out and (expect is None or expect in out)
                    rec("fuzz", ok, f"{desc}: {out[:90]!r}")
                except Exception as e:
                    rec("fuzz", False, f"{desc}: client exception {type(e).__name__}: {e}")
            await session.send_ping()
            rec("fuzz", sqlite_counts() == before, f"index intact after fuzzing (row counts {before})")

            # 4. concurrency
            mix = [(t, a) for t, a, _, _ in GOLDEN if t not in ("bg3_layers", "bg3_similar_spells")]
            jobs = [mix[i % len(mix)] for i in range(400)]
            sem = asyncio.Semaphore(32)
            lat, bad = [], []

            async def one(t, a):
                async with sem:
                    out, err, dt = await call(session, t, a)
                    lat.append(dt)
                    if err or "internal error" in out or "error:" in out[:6]:
                        bad.append((t, out[:120]))
            t1 = time.perf_counter()
            await asyncio.gather(*(one(t, a) for t, a in jobs))
            wall = time.perf_counter() - t1
            q = statistics.quantiles(lat, n=100)
            rec("concurrency", not bad, f"400 calls x32 in {wall:.1f}s ({400/wall:.0f}/s); p50 {q[49]*1000:.0f} ms, p95 {q[94]*1000:.0f} ms, max {max(lat)*1000:.0f} ms"
                + (f"; failures {bad[:3]}" if bad else ""))
            rec("memory", True, f"server RSS {rss_mb()} MB")

            # 5. refresh under load
            load = [one(t, a) for t, a in (mix * 6)[:80]]
            bad.clear()
            refresh = call(session, "bg3_refresh", {"force": "apotheosis"})
            res = await asyncio.gather(refresh, *load)
            rout = res[0][0]
            rec("refresh-under-load", "rebuilt: ['apotheosis']" in rout and not bad, f"forced apotheosis rebuild alongside 80 queries; failures {bad[:2]}")

            # 5b. Script Extender bridge (only when the game is running)
            st, _, _ = await call(session, "bg3_se_status")
            if "game running" in st and "no Extender Runtime log" not in st:
                out, err, dt = await call(session, "bg3_se_eval", {"code": "return 6*7"})
                rec("se", "OK: 42" in out, f"eval round-trip {dt:.1f}s: {out[:40]!r}")
                out, _, _ = await call(session, "bg3_se_eval", {"code": 'error("boom")'})
                rec("se", "LUA ERROR" in out and "boom" in out, "Lua errors come back as errors, not timeouts")
                out, _, _ = await call(session, "bg3_se_eval", {"code": 'Ext.Utils.Print("stress-print"); return true'})
                rec("se", "stress-print" in out, "printed output captured")
                out, _, dt = await call(session, "bg3_se_live_entry", {"name": "Target_ChillTouch", "layers": ["dnd55e"]})
                rec("se", "SpellRoll" in out and "MATCH" in out, f"live entry vs index ({dt:.1f}s): {out.splitlines()[1] if len(out.splitlines())>1 else out}")
                out, _, _ = await call(session, "bg3_se_log", {"filter": "BG3SE", "lines": 5})
                rec("se", "BG3SE" in out, "log tail with filter")
                # console access is serialised: concurrent evals must all succeed
                res = await asyncio.gather(*(call(session, "bg3_se_eval", {"code": f"return {i}"}) for i in range(6)))
                rec("se", all(f"OK: {i}" in r[0] for i, r in enumerate(res)), f"6 concurrent evals serialised ({sum(r[2] for r in res):.1f}s total)")
            else:
                rec("se", True, f"SKIPPED (game not running): {st.splitlines()[0]}")

            # 6. layer management with a real .pak
            paks = glob.glob("/mnt/d/vortex/BG3/mods/PHB_2024_Bladesinger*/*.pak")
            cfg_before = json.load(open(sources.CONFIG))
            if paks:
                out, err, dt = await call(session, "bg3_add_mod_layer", {"name": "stress_bladesinger", "path": paks[0]})
                pak_stats = next((l for l in out.splitlines() if "stress_bladesinger" in l), "")
                idx = out.splitlines().index(pak_stats) if pak_stats else -1
                counts = out.splitlines()[idx + 1] if idx >= 0 else ""
                rec("layers", "stress_bladesinger" in out and not err and "stats 0," not in counts, f"added .pak layer in {dt:.1f}s: {counts.strip()}")
                out, err, _ = await call(session, "bg3_add_mod_layer", {"name": "stress_bladesinger", "path": paks[0]})
                rec("layers", "already exists" in out, "duplicate layer rejected")
                out, err, _ = await call(session, "bg3_search", {"text": "a", "defined_in": "stress_bladesinger", "limit": 5})
                rec("layers", "stress_bladesinger" in out and "[base]" not in out, f"defined_in scopes to the .pak layer: {out.splitlines()[:2]}")
                first = out.splitlines()[0].split(" (")[0] if out and "no matches" not in out else None
                if first:
                    d, _, _ = await call(session, "bg3_diff", {"name": first, "layer": "stress_bladesinger", "layers": ["stress_bladesinger"]})
                    rec("layers", "internal error" not in d, f"diff on .pak entry {first}: {d.splitlines()[0][:100]}")
                out, err, _ = await call(session, "bg3_remove_mod_layer", {"name": "stress_bladesinger"})
                rec("layers", "stress_bladesinger" not in out, "removed .pak layer")
            else:
                rec("layers", True, "SKIPPED: no Bladesinger .pak found in Vortex staging")
            rec("layers", json.load(open(sources.CONFIG)) == cfg_before, "layers.json content restored")


def sqlite_counts():
    import sqlite3
    db = sqlite3.connect(os.path.join(sources.CACHE, "index.sqlite"))
    return {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("stats", "loca", "templates", "prog", "lists", "mei", "fx")}


def sweep():
    s = query.Store(refresh=False)
    stacks = {"base": [], "base+dnd55e": ["dnd55e"], "all": None}
    names = [r[0] for r in s.db.execute("SELECT DISTINCT name FROM stats")]
    for label, layers in stacks.items():
        active = s.active(layers)
        t0 = time.perf_counter()
        n = exc = unresolved = 0
        samples = []
        for name in names:
            try:
                r = s.resolve(name, active)
            except Exception as e:
                exc += 1
                samples.append(f"{name}: {e}")
                continue
            if r:
                n += 1
                if any("[UNRESOLVED]" in c for c in r["chain"]):
                    unresolved += 1
                    if len(samples) < 6:
                        samples.append(r["chain"][0])
        rec("sweep", exc == 0, f"{label}: resolved {n} entries in {time.perf_counter()-t0:.1f}s, exceptions {exc}, unresolved `using` {unresolved}"
            + (f"; e.g. {samples[:4]}" if samples else ""))
    active = s.active(None)
    t0 = time.perf_counter()
    keys = [r[0] for r in s.db.execute("SELECT DISTINCT mapkey FROM templates")]
    broken = sum(1 for k in keys if any("NOT FOUND" in c for c in (s.template(k, active) or {"chain": []})["chain"]))
    rec("sweep", True, f"templates: {len(keys)} chains walked in {time.perf_counter()-t0:.1f}s; {broken} reference a missing parent (vanilla has some)")
    t0 = time.perf_counter()
    meis = [r[0] for r in s.db.execute("SELECT DISTINCT uuid FROM mei")]
    comps = missing = 0
    for g in meis:
        e = s.effect(g, active)
        for c in e["effects"]:
            comps += 1
            missing += c["name"] is None
    rec("sweep", True, f"effects: {len(meis)} MultiEffectInfos, {comps} components, {missing} with no indexed resource ({missing/max(comps,1):.1%}) in {time.perf_counter()-t0:.1f}s")
    tables = [r[0] for r in s.db.execute("SELECT DISTINCT table_uuid FROM prog")]
    t0 = time.perf_counter()
    for t in tables:
        s.progression(t, active)
    rec("sweep", True, f"progressions: {len(tables)} tables merged in {time.perf_counter()-t0:.1f}s")


def main():
    started = datetime.now().astimezone()
    asyncio.run(mcp_tests())
    sweep()
    ok = sum(1 for _, o, _ in report if o)
    lines = [f"# bg3-data-mcp stress report", "", f"Run {started:%Y-%m-%d %H:%M %Z}. {ok}/{len(report)} checks passed.", ""]
    sec = None
    for section, o, detail in report:
        if section != sec:
            lines += ["", f"## {section}"]
            sec = section
        lines.append(f"- {'PASS' if o else '**FAIL**'} {detail}")
    open(os.path.join(REPO, "tests", "STRESS_REPORT.md"), "w").write("\n".join(lines) + "\n")
    print(f"\n{ok}/{len(report)} passed; failures: {failures or 'none'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
