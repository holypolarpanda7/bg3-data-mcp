"""Generic in-game testing for any mod layer: harness install, level-up by XP, progression checks,
data-driven test cases (TOML) staged as real encounters, and human-readable test scripts.

Fidelity is explicit. A scripted cast (Osi.UseSpell) never pays costs and skips turn flow, so only a
player cast from the hotbar can verify slots/resources; every verdict says which kind of run it was.
Test suites live in each mod repo (default `<mod path>/tests/bg3/*.toml`, or `tests` in layers.json).
"""
import glob
import hashlib
import json
import math
import os
import re
import subprocess
import time
import tomllib

from . import parse, se, sources

HERE = os.path.dirname(__file__)
HARNESS = os.path.join(HERE, "lua", "harness.lua")
STATE_FILE = os.path.join(sources.CACHE, "test_state.json")

TEMPLATES = {  # verified base-game creatures; any template GUID also works
    "wolf": ("9beee5c9-279e-49d8-a4a8-18f9ee0b1519", "Wolf"),
    "boar": ("be71d66c-5328-490f-b962-4fdb7ca2647f", "Boar"),
    "bear": ("ca66e982-91f6-4b60-8ebc-d4c4f2568f0c", "Bear"),
    "skeleton": ("6c06cda2-6e13-4663-a6f6-c4bb7564c10f", "Skeleton"),
    "zombie": ("c2a2c269-ede8-4887-99f1-e0c044cc0c75", "Zombie"),
}
FACTIONS = {
    "hostile": "64321d50-d516-b1b2-cfac-2eb773de1ff6",
    "friendly": "80182081-6bb1-95f1-c40f-4c3cea368269",
    "neutral": "a66b2d45-1b6c-082d-8a01-c6d975ead314",
}
MODES = ("ai", "auto", "player", "script")
SCRIPT_SPELL_SOURCES = {"Osiris"}  # spells added by script (Osi.AddSpell): not class-sourced
GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


# ------------------------------------------------------------------ config
def game_cfg():
    """Launch/lifecycle settings: layers.json "game" overrides, otherwise discovered for this machine."""
    from . import platform
    cfg = sources.load_config()
    g = dict(cfg.get("game") or {})
    install = os.path.dirname(cfg["base"]["game_data"])
    store = cfg["base"].get("_store") or ("steam" if "steamapps" in install.lower() else "direct")
    g.setdefault("launcher", "auto")
    if g["launcher"] == "auto":
        g["launcher"] = "steam" if store == "steam" and platform.steam_dir() else "direct"
    sd = platform.steam_dir()
    g["steam_exe"] = platform.to_native(g["steam_exe"]) if g.get("steam_exe") else (os.path.join(sd, "steam.exe") if sd else None)
    g["game_exe"] = platform.to_native(g["game_exe"]) if g.get("game_exe") else platform.game_exe(install)
    g.setdefault("app_id", platform.STEAM_APP_ID)
    g.setdefault("launch_args", ["--skip-launcher", "-continueGame"])
    ld = platform.larian_dir(cfg)
    g["savegames"] = platform.to_native(g["savegames"]) if g.get("savegames") else \
        os.path.join(ld or "", "PlayerProfiles", g.get("profile", "Public"), "Savegames", "Story")
    g.setdefault("processes", ["bg3.exe", "bg3_dx11.exe"])
    return cfg, g


def mod_entry(layer):
    cfg = sources.load_config()
    for m in cfg["mods"]:
        if m["name"] == layer:
            return m
    raise ValueError(f"unknown mod layer {layer!r} (see bg3_layers)")


# ------------------------------------------------------------------ harness
def _harness_source():
    src = open(HARNESS, encoding="utf-8").read()
    version = hashlib.sha1(src.encode()).hexdigest()[:12]
    return src.replace("__VERSION__", version), version


_HARNESS_SEEN = {"key": None, "at": 0.0}


def ensure_harness():
    """Install (or upgrade) the harness in the running game. The check costs a console round trip, so a
    confirmed (game process, version) is trusted for 60 s; a failing call (e.g. after a Lua reset) re-checks."""
    src, version = _harness_source()
    proc = se.game_process()
    key = (proc[0] if proc else None, version)
    if _HARNESS_SEEN["key"] == key and time.time() - _HARNESS_SEEN["at"] < 60:
        return version
    r = se.eval_lua(f"return BG3T and BG3T.version or ''", "server", timeout=15)
    if r["ok"] and r["result"] == version:
        _HARNESS_SEEN.update(key=key, at=time.time())
        return version
    cfg = sources.load_config()
    folder = os.path.join(cfg["base"]["game_data"], "Public", "BG3DataTest")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "harness.lua"), "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    r = se.eval_lua('local s=Ext.IO.LoadFile("Public/BG3DataTest/harness.lua","data"); '
                    'if not s then return "harness file not readable" end; local f,e=load(s); '
                    'if not f then return "compile: "..tostring(e) end; local r=f(); '
                    'return {r=r, errors=BG3T and BG3T.listen_errors}', "server", timeout=20)
    if not r["ok"] or not isinstance(r["result"], dict):
        raise RuntimeError(f"harness install failed: {r['result']}")
    _HARNESS_SEEN.update(key=key, at=time.time())
    errs = r["result"].get("errors") or []
    if errs:
        raise RuntimeError("harness installed but some Osiris listeners failed: " + "; ".join(map(str, errs)))
    return version


def lua(code, timeout=20):
    """Eval with the harness guaranteed present; raise on Lua errors."""
    ensure_harness()
    r = se.eval_lua(code, "server", timeout=timeout)
    if not r["ok"] and "BG3T" in str(r["result"]):  # harness gone (Lua reset): reinstall once and retry
        _HARNESS_SEEN["key"] = None
        ensure_harness()
        r = se.eval_lua(code, "server", timeout=timeout)
    if not r["ok"]:
        raise RuntimeError(f"in-game error: {r['result']}")
    return r["result"]


def host_state():
    # Ext.StaticData lookups occasionally come back empty for a tick: retry until class names resolve
    for _ in range(3):
        st = lua("return BG3T.snapshot(BG3T.host(), true)", timeout=30)
        cl = st.get("classes") or []
        if cl and all(c.get("class") for c in cl) and st.get("spells"):
            return st
        time.sleep(1.0)
    raise RuntimeError(f"host class data didn't resolve: {st.get('classes')}")


# ------------------------------------------------------------------ XP / levels
def xp_table(store, active):
    """{level: XP needed to go from level to level+1}, later layers overriding earlier ones."""
    cfg = store.cfg
    files = [p for _, p in sources.base_files(cfg, "stats") if os.path.basename(p).lower() == "xpdata.txt"]
    for m in cfg["mods"]:
        if m["name"] in active:
            files += [p for p in sources.mod_files(cfg, m, "stats") if os.path.basename(p).lower() == "xpdata.txt"]
    table, max_level = {}, None
    for f in files:
        for k, v in re.findall(r'key\s+"(\w+)"\s*,\s*"(\d+)"', parse.read_text(f)):
            if k.startswith("Level") and k[5:].isdigit():
                table[int(k[5:])] = int(v)
            elif k == "MaxXPLevel":
                max_level = int(v)
    return table, max_level


def grant_levels(store, active, levels=1):
    st = host_state()
    table, max_level = xp_table(store, active)
    level, xp = st["level"], st.get("xp") or 0
    target = level + levels
    if max_level and target > max_level:
        raise ValueError(f"XPData caps at level {max_level} for layers {active}")
    need = sum(table.get(l, 0) for l in range(1, target))
    amount = need - xp
    if amount <= 0:
        return {"level": level, "xp": xp, "granted": 0, "target": target, "note": "already has enough XP - level up in the UI"}
    lua(f"Osi.AddExplorationExperience(BG3T.host(), {amount}); return true")
    time.sleep(1.5)
    after = lua("local e=Ext.Entity.Get(BG3T.host()); return e.Experience.TotalExperience")
    return {"level": level, "xp": xp, "granted": amount, "xp_after": after, "target": target, "needed_total": need}


def _boost_resources(boosts):
    """'ActionResource(SpellSlot,1,1);...' -> [(name, level, amount)]"""
    return [(n, int(l), float(a)) for n, a, l in re.findall(r"ActionResource\(\s*(\w+)\s*,\s*([\d.]+)\s*,\s*(\d+)\s*\)", boosts or "")]


def _list_spells(store, active, uuid):
    row = store.spell_list(uuid, active)
    if not row:
        return None
    attrs = json.loads(row[4])
    return [s for s in re.split(r"[;,]", attrs.get("Spells", "")) if s]


def level_check(store, active):
    """Compare the host with what its class/subclass progressions grant up to its current level."""
    st = host_state()
    have_p = set(st["passives"])
    have_s = {s["id"]: s["source"] for s in st["spells"]}
    lines, fails, warns = [], 0, 0
    lines.append(f"host level {st['level']}  (region {st['region']}, XP {st.get('xp')})")
    if any(s.startswith("TUT_SUMMON_BLOCK") for s in st["statuses"]):
        lines.append("  note: tutorial region (TUT_SUMMON_BLOCK) - summon spells can't be tested here")
    for c in st["classes"]:
        tables = [(c["class"], c["class_table"])] + ([(c["subclass"], c["subclass_table"])] if c.get("subclass") else [])
        lines.append(f"{c['class']} {c['level']}" + (f" / {c['subclass']}" if c.get("subclass") else " (no subclass yet)"))
        added, removed, res, spell_lists, choices = {}, set(), {}, [], []
        for name, table in tables:
            for lvl, pname, _, src, a in store.progression(table, active):
                if lvl > c["level"] or str(a.get("IsMulticlass", "")).lower() == "true":
                    continue
                for p in filter(None, (a.get("PassivesAdded") or "").split(";")):
                    added[p] = (lvl, src)
                for p in filter(None, (a.get("PassivesRemoved") or "").split(";")):
                    removed.add(p)
                for rname, rlvl, amt in _boost_resources(a.get("Boosts")):
                    res[(rname, rlvl)] = res.get((rname, rlvl), 0) + amt
                for sel in re.findall(r"(\w+)\(([^)]*)\)", a.get("Selectors") or ""):
                    kind, args = sel[0], [x.strip() for x in sel[1].split(",")]
                    if kind == "AddSpells" and args and args[0]:
                        spell_lists.append((lvl, args[0], src))
                    elif kind.startswith("Select") and lvl == c["level"]:
                        choices.append(f"{kind}({', '.join(args[:2])}) [{src}]")
        for p, (lvl, src) in sorted(added.items(), key=lambda t: t[1][0]):
            if p in removed:
                continue
            ok = p in have_p
            fails += not ok
            if not ok or lvl == c["level"]:
                lines.append(f"  {'PASS' if ok else 'FAIL'} passive {p} (L{lvl}, {src})")
        for p in sorted(removed):
            if p in have_p:
                fails += 1
                lines.append(f"  FAIL passive {p} should have been removed (PassivesRemoved)")
        for lvl, uuid, src in spell_lists:
            spells = _list_spells(store, active, uuid)
            if spells is None:
                warns += 1
                lines.append(f"  WARN AddSpells list {uuid} (L{lvl}) not in index")
                continue
            for sp in spells:
                ok = sp in have_s
                fails += not ok
                if not ok or lvl == c["level"]:
                    lines.append(f"  {'PASS' if ok else 'FAIL'} spell {sp} (L{lvl} AddSpells, {src})" + (f" source={have_s[sp]}" if ok else ""))
        # resource boosts from the host's other passives (origin feats, race, background...) also count
        other = {}
        for p in have_p - set(added):
            r = store.resolve(p, active)
            for rname, rlvl, amt in _boost_resources((r or {}).get("fields", {}).get("Boosts", ("", ""))[0]):
                other.setdefault((rname, rlvl), []).append((p, amt))
        for (rname, rlvl), amt in sorted(res.items()):
            got = (st["resources"].get(rname) or {}).get(str(rlvl))
            mx = got[1] if got else 0
            extra = other.get((rname, rlvl), [])
            total = amt + sum(a for _, a in extra)
            why = ", ".join(f"+{a:g} {p}" for p, a in extra)
            if mx == total and extra:
                lines.append(f"  PASS resource {rname}[{rlvl}] max {mx:g} = progression {amt:g} {why}")
            elif mx != total:
                warns += 1
                lines.append(f"  WARN resource {rname}[{rlvl}] max {mx:g}, expected {total:g} (progression {amt:g}"
                             + (f" {why}" if why else "") + ")")
        for ch in choices:
            lines.append(f"  info this level's choice: {ch}")
    lines.insert(0, f"LEVEL CHECK: {'ALL PASS' if not fails else f'{fails} FAIL'}" + (f", {warns} warning(s)" if warns else ""))
    return "\n".join(lines)


# ------------------------------------------------------------------ suites
def suite_dirs(layer):
    m = mod_entry(layer)
    root = m["path"] if not m["path"].lower().endswith(".pak") else None
    if m.get("tests"):
        return [m["tests"] if os.path.isabs(m["tests"]) else os.path.join(root or "", m["tests"])]
    return [os.path.join(root, "tests", "bg3")] if root else []


def load_cases(layer):
    cases = []
    for d in suite_dirs(layer):
        for f in sorted(glob.glob(os.path.join(d, "*.toml"))):
            with open(f, "rb") as fh:
                doc = tomllib.load(fh)
            suite = doc.get("suite", {})
            for c in doc.get("case", []):
                c = {**{k: v for k, v in suite.items() if k in ("class", "subclass", "mode")}, **c}
                c["_file"] = os.path.relpath(f, d)
                c["_suite"] = suite.get("name") or os.path.splitext(os.path.basename(f))[0]
                cases.append(c)
    return cases


def load_builds(layer):
    builds = []
    for d in suite_dirs(layer):
        for f in sorted(glob.glob(os.path.join(d, "*.toml"))):
            with open(f, "rb") as fh:
                doc = tomllib.load(fh)
            suite = doc.get("suite", {})
            for b in doc.get("build", []):
                b = {**{k: v for k, v in suite.items() if k in ("class",)}, **b}
                b.setdefault("levels", [1, 20])
                builds.append(b)
    return builds


def find_build(layer, build_id):
    for b in load_builds(layer):
        if b["id"] == build_id:
            return b
    raise ValueError(f"no build {build_id!r} in {suite_dirs(layer)}")


def assign_cases(cases, builds):
    """{build id: [cases]}. A case goes to its `build`, else a build of its `subclass`, else (class-wide)
    to the builds covering its level in turn, so shared tests are spread across runs instead of repeated."""
    out = {b["id"]: [] for b in builds}
    turn = {}
    for c in cases:
        cls = (c.get("class") or "").lower()
        mine = [b for b in builds if (b.get("class") or "").lower() == cls]
        if c.get("build"):
            if c["build"] in out:
                out[c["build"]].append(c)
            continue
        if c.get("subclass"):
            hit = [b for b in builds if b.get("subclass") == c["subclass"]]
            if hit:
                out[hit[0]["id"]].append(c)
            continue
        lvl = c.get("level", 1)
        cover = [b for b in mine if b["levels"][0] <= lvl <= b["levels"][1]]
        if cover:
            k = (cls, lvl)
            b = cover[turn.get(k, 0) % len(cover)]
            turn[k] = turn.get(k, 0) + 1
            out[b["id"]].append(c)
    return out


def find_case(layer, case_id):
    for c in load_cases(layer):
        if c.get("id") == case_id:
            return c
    raise ValueError(f"no test case {case_id!r} in {suite_dirs(layer)}")


EXPECT_KEYS = {"target", "dead", "hp_change", "hp", "status_present", "status_absent", "status_applied", "status_applied_any",
               "status_removed", "resource", "level", "change", "cast", "acted_first", "damage_type", "note", "max_hp_change",
               "roll", "pass", "consecutive_turns", "count", "took_turn",
               "status_applied_count", "cast_count", "saves", "cast_only", "interrupt_used",
               "amount_change", "max_change", "skill", "ability", "damage_count", "temp_hp", "temp_hp_change"}
ROLL_TYPES = {"SavingThrow", "SkillCheck", "RawAbility"}


def validate(store, active, c):
    """Problems that would make a case unrunnable, checked against the index."""
    errs = []
    for k in ("id", "title"):
        if not c.get(k):
            errs.append(f"missing `{k}`")
    aliases = {"host"} | {s.get("as") for s in c.get("spawn", [])}
    if c.get("mode") == "ai":
        if not c.get("caster") or c.get("caster") == "host":
            errs.append("mode ai needs `caster` = a spawn alias (the host is player-controlled)")
        for sp in [c.get("spell")] + list(c.get("ai_keep", [])):
            sf = spell_facts(store, active, sp) if sp else None
            if sf and not sf["ai_can_use"]:
                errs.append(f"{sp} has AIFlags CanNotUse - the AI never casts it; use mode script + real_rolls")
            if sf and sf["requirements"]:
                errs.append(f"{sp} has RequirementConditions ({sf['requirements'][:60]}) - the AI may never meet them")
        sf = spell_facts(store, active, c["spell"]) if c.get("spell") else None
        for e in c.get("expect", []):
            sv = e.get("saves") or {}
            if sf and sv.get("spell_only") and sv.get("ability") and sv["ability"] not in sf["save_abilities"]:
                errs.append(f"{c['spell']}'s own roll isn't a {sv['ability']} save ({sf['roll'] or 'no SpellRoll'}) - "
                            f"spell_only saves will never be recorded; see bg3_save_spells")
    for who in [c.get("caster")] + [cs.get("by") for cs in c.get("casts", [])]:
        if who and who not in aliases:
            errs.append(f"caster `{who}` isn't host or a spawn alias")
    if c.get("console") and not c.get("expect_log"):
        errs.append("console case needs `expect_log` (the log line that means PASS)")
    if c.get("spell") and not store.resolve(c["spell"], active):
        errs.append(f"spell {c['spell']} not found in layers {active}")
    if c.get("target") and c["target"] not in aliases | {"ground"}:
        errs.append(f"target {c['target']!r} is not host or a spawn alias")
    for s in c.get("spawn", []):
        t = s.get("template", "")
        if t not in TEMPLATES and not GUID.match(t):
            errs.append(f"spawn {s.get('as')}: template {t!r} is not an alias ({', '.join(TEMPLATES)}) or GUID")
        if s.get("faction", "hostile") not in FACTIONS and not GUID.match(s.get("faction", "")):
            errs.append(f"spawn {s.get('as')}: unknown faction {s.get('faction')!r}")
    for e in c.get("expect", []):
        bad = set(e) - EXPECT_KEYS
        if bad:
            errs.append(f"expect: unknown keys {sorted(bad)}")
        if e.get("target", "host") not in aliases:
            errs.append(f"expect target {e.get('target')!r} unknown")
    for cs in c.get("casts", []):
        if not cs.get("spell") or not store.resolve(cs["spell"], active):
            errs.append(f"casts: spell {cs.get('spell')!r} not found")
        if cs.get("target", "host") not in aliases | {"ground"}:
            errs.append(f"casts: target {cs.get('target')!r} unknown")
    tags = set()
    for r in c.get("rolls", []):
        tags.add(r.get("as"))
        if r.get("type") not in ROLL_TYPES:
            errs.append(f"rolls {r.get('as')}: type must be one of {sorted(ROLL_TYPES)}")
        if not r.get("id") or not isinstance(r.get("dc"), int):
            errs.append(f"rolls {r.get('as')}: needs id (ability or skill) and an integer dc")
    for e in c.get("expect", []):
        if "roll" in e and e["roll"] not in tags:
            errs.append(f"expect roll {e['roll']!r} isn't a rolls alias")
    for st in c.get("setup", []):
        for s in ([st.get("status")] if st.get("status") else []):
            if not store.resolve(s, active):
                errs.append(f"setup status {s} not found")
    return errs


def list_cases(store, active, layer, cls=None, level=None):
    out = []
    for c in load_cases(layer):
        if cls and (c.get("class") or "").lower() != cls.lower():
            continue
        if level is not None and c.get("level") != level:
            continue
        errs = validate(store, active, c)
        out.append(f"{c['id']}  L{c.get('level', '?')} {c.get('class', '')}  [{c.get('mode', 'auto')}] {c.get('title', '')}"
                   + ("" if not errs else "\n    INVALID: " + "; ".join(errs)))
    return "\n".join(out) or f"no cases (suite folders: {suite_dirs(layer)})"


# ------------------------------------------------------------------ staging / verifying
def _save_state(d):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump(d, open(STATE_FILE, "w"))


def _load_state():
    try:
        return json.load(open(STATE_FILE))
    except (OSError, ValueError):
        return None


def _name(store, active, stat):
    r = store.resolve(stat, active)
    return (store.display_name(r["fields"], active) if r else None) or stat


def stage(store, active, layer, case_id):
    c = find_case(layer, case_id)
    errs = validate(store, active, c)
    if errs:
        raise ValueError("case is invalid: " + "; ".join(errs))
    mode = c.get("mode", "auto")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    pre = lua("return BG3T.precheck()", timeout=30) or {}  # cleanup + snapshot + statuses in one round trip
    st = pre.get("snapshot") or {}
    if not (st.get("classes") and all(x.get("class") for x in st["classes"]) and st.get("spells")):
        st = host_state()  # class names occasionally resolve a tick late: the retrying path
    notes, blockers = [], []
    cl = next((x for x in st["classes"] if not c.get("class") or x["class"] == c["class"]), None)
    if c.get("class") and not cl:
        blockers.append(f"host has no {c['class']} levels")
    elif cl and c.get("level") and cl["level"] < c["level"]:
        blockers.append(f"host is {cl['class']} {cl['level']}; case needs level {c['level']}")
    elif cl and c.get("level") and cl["level"] > c["level"]:
        notes.append(f"host is {cl['class']} {cl['level']} (case written for {c['level']})")
    if c.get("subclass") and not any(x.get("subclass") == c["subclass"] for x in st["classes"]):
        blockers.append(f"host lacks subclass {c['subclass']}")
    if c.get("spell") and c.get("caster", "host") == "host":
        src = {s["id"]: s["source"] for s in st["spells"]}.get(c["spell"])
        granter = None  # a setup status/passive whose UnlockSpell grants the spell (e.g. a feature's unlock status)
        for s in c.get("setup", []):
            name = s.get("status") or s.get("passive")
            r = store.resolve(name, active) if name else None
            boosts = str(((r or {}).get("fields") or {}).get("Boosts", ("",))[0])
            if f"UnlockSpell({c['spell']}" in boosts.replace(" ", ""):
                granter = name
        if src is None and granter:
            notes.append(f"{c['spell']} is granted by setup {granter} (UnlockSpell)")
        elif src is None and mode != "script":
            blockers.append(f"host doesn't know {c['spell']} - learn it through level-up / the class spell list")
        elif src in SCRIPT_SPELL_SOURCES and mode != "script":
            blockers.append(f"{c['spell']} was added by script (source {src}): it won't pay costs - learn it via level-up")
        elif src:
            notes.append(f"{c['spell']} known, source {src}")
    if st["in_combat"]:
        blockers.append("host is already in combat - finish or reload first")
    if blockers:
        return {"ok": False, "blockers": blockers, "notes": notes}

    pre_statuses = pre.get("statuses") or []  # anything not here at the end was added by the run
    spawns = c.get("spawn", [])
    hostile = any(s.get("faction", "hostile") == "hostile" for s in spawns)
    combat = c.get("combat", hostile)
    first = combat and c.get("initiative", "host_first") == "host_first"
    floor = int(c.get("safety_floor", 35))
    # Order matters: a hostile spawn starts combat (and rolls initiative) the moment it appears. So the
    # recorder, safety watch and initiative boost go first, creatures spawn NEUTRAL, get their setup, and
    # only then switch to their real faction and enter combat.
    code = [f"BG3T.safety.floor={floor}; BG3T.safety.enabled={'true' if c.get('safety', True) else 'false'}; "
            "local since=BG3T.seq; BG3T.recording=true; local r={}"]
    if first:
        code.append("BG3T.grant(BG3T.host(), 'Initiative(50)')")
    for i, s in enumerate(spawns):
        tpl = TEMPLATES.get(s["template"], (s["template"], None))[0]
        dist = float(s.get("distance", 8))
        ang = (i - (len(spawns) - 1) / 2) * 0.6
        dx, dz = dist * math.cos(ang), dist * math.sin(ang)
        a_ = se._lua_string(s["as"])
        code.append(f"r[{a_}]=BG3T.spawn({a_},{se._lua_string(tpl)},{se._lua_string(FACTIONS['neutral'])},{dx:.2f},{dz:.2f})")
        if s.get("hp") is not None:
            code.append(f"if r[{a_}] then local m=Osi.GetMaxHitpoints(r[{a_}]); "
                        f"if {int(s['hp'])}>m then BG3T.grant(r[{a_}],'IncreaseMaxHP('..({int(s['hp'])}-m)..')') end end")
    for st_ in c.get("setup", []):
        if st_.get("passive"):
            code.append(f"BG3T.addPassive({_who(st_)}, {se._lua_string(st_['passive'])})")
        if st_.get("max_hp") and st_.get("target", "host") == "host":
            code.append(f"BG3T.grant(BG3T.host(), 'IncreaseMaxHP({int(st_['max_hp'])})')")
    code.append("return {r=r, since=since}")
    res = lua("; ".join(code), timeout=30)
    spawned, since = res.get("r") or {}, res["since"]
    missing = [s["as"] for s in spawns if not (spawned or {}).get(s["as"])]
    if missing:
        lua("BG3T.cleanup(); return true")
        raise RuntimeError(f"spawn failed for {missing}")
    time.sleep(0.4)  # boosts (initiative, max HP) apply on the next tick (a frame; was 1.2 s)
    post = []
    for s in spawns:
        if s.get("hp") is not None:
            post.append(f"BG3T.setHp(BG3T.spawns[{se._lua_string(s['as'])}], {int(s['hp'])})")
    if c.get("refill", True):  # first, so setup steps (e.g. a resource set to 0) aren't undone by the top-up
        post.append("BG3T.refill(BG3T.host()); BG3T.clearCooldowns(BG3T.host())")
    for st_ in c.get("setup", []):
        if "remove_status" in st_:  # exact names, or a prefix ending in * (e.g. ARCANE_WARD*)
            for name in ([st_["remove_status"]] if isinstance(st_["remove_status"], str) else st_["remove_status"]):
                post.append(f"BG3T.removeStatuses({_who(st_)}, {se._lua_string(name)})")
        if st_.get("max_hp") and st_.get("target", "host") != "host":
            post.append(f"BG3T.grant({_who(st_)}, 'IncreaseMaxHP({int(st_['max_hp'])})')")
        if "hp" in st_:
            post.append(f"BG3T.setHp({_who(st_)}, {st_['hp'] if isinstance(st_['hp'], int) else repr('full')})")
        if "resource" in st_ and "amount" in st_:  # e.g. { target = "host", resource = "SpellSlot", level = 1, amount = 0 }
            post.append(f"BG3T.setResource({_who(st_)}, {se._lua_string(st_['resource'])}, {int(st_.get('level', 0))}, {float(st_['amount'])})")
        if st_.get("status"):
            post.append(f"BG3T.apply({_who(st_)}, {se._lua_string(st_['status'])}, {int(st_.get('turns', 10))})")
        if st_.get("boost"):
            post.append(f"BG3T.grant({_who(st_)}, {se._lua_string(st_['boost'])})")
        if st_.get("dead"):  # e.g. a corpse for revive spells
            post.append(f"pcall(Osi.Die, {_who(st_)}, 0, 'NULL_00000000-0000-0000-0000-000000000000', 0, 1)")
    # a case that deliberately starts you at low HP mustn't trip the safety watch by itself
    post.append("local h=BG3T.host(); local p=Osi.GetHitpoints(h)*100/math.max(1,Osi.GetMaxHitpoints(h)); "
                "if p<=BG3T.safety.floor then BG3T.safety.floor=math.max(1,math.floor(p)-1) end")
    for s in spawns:
        fac = FACTIONS.get(s.get("faction", "hostile"), s.get("faction"))
        if fac != FACTIONS["neutral"]:
            post.append(f"pcall(Osi.SetFaction, BG3T.spawns[{se._lua_string(s['as'])}], {se._lua_string(fac)})")
    if combat:
        post.append("BG3T.enterCombat()")
    post.append("return true")
    lua("; ".join(post), timeout=30)
    # your own reactions off unless the case names the ones it measures (`reactions = ["Fate"]`, or ["*"]): a
    # Shield / Arcane or Projected Ward / opportunity attack changes the rolls a case checks (Last Stand's bite was
    # eaten by Projected Ward, 2026-10-02). AI mode keeps them all on unless the case lists some.
    keep = c.get("reactions", ["*"] if mode == "ai" else [])
    time.sleep(0.6)  # a freshly added passive's interrupt appears a moment later
    lua(f"return BG3T.setReactions(BG3T.host(), {{{', '.join(se._lua_string(k) for k in keep)}}})", timeout=15)
    time.sleep(0.2)
    before = lua("return BG3T.world()", timeout=30)
    first_turn = None
    if combat:
        for _ in range(12):
            r_ = se.eval_lua(f"return BG3T.drain({since})", "server", timeout=10)
            evs = (r_["result"] if r_["ok"] else None) or []
            turns = [e for e in evs if e.get("kind") == "TurnStarted"]
            if turns:
                first_turn = turns[0]
                break
            time.sleep(0.5)
    state = {"layer": layer, "case": c["id"], "mode": mode, "combat": combat, "since": since, "before": before, "pre_statuses": pre_statuses,
             "first_turn": first_turn, "staged_at": time.time()}
    _save_state(state)
    if c.get("grant_passive"):  # the case's action is gaining a feature (e.g. a boon's max HP increase)
        lua(f"BG3T.addPassive(BG3T.host(), {se._lua_string(c['grant_passive'])}); return true")
    for i, cs in enumerate(c.get("casts", [])):  # a scripted sequence before the case's own spell (any mode)
        if i:  # event-driven: the previous cast resolved (old fixed `wait` is now only the cap)
            prev = c["casts"][i - 1]["spell"]
            _wait_casts(since, prev, sum(1 for x in c["casts"][:i] if x["spell"] == prev), float(c["casts"][i - 1].get("wait", 2)) + 4)
            _settle(since, quiet=0.5, cap=3)
        tgt = cs.get("target", "host")
        by = "BG3T.host()" if cs.get("by", "host") == "host" else f"BG3T.spawns[{se._lua_string(cs['by'])}]"
        if tgt == "ground":  # e.g. a Darkness cloud on the host's spot: distance = 0
            lua(f"BG3T.castAt({by}, {se._lua_string(cs['spell'])}, {float(cs.get('distance', 0))}); return true")
            continue
        tgt = "BG3T.host()" if tgt == "host" else f"BG3T.spawns[{se._lua_string(tgt)}]"
        lua(f"BG3T.cast({by}, {se._lua_string(cs['spell'])}, {tgt}, {'true' if cs.get('real_rolls') else 'false'}); return true")
    if c.get("casts"):
        last = c["casts"][-1]["spell"]
        _wait_casts(since, last, sum(1 for x in c["casts"] if x["spell"] == last), float(c.get("casts_wait", 2)) + 4)
        _settle(since, quiet=0.5, cap=3)
    for r in c.get("rolls", []):  # passive rolls after setup (any mode), tallied by the harness
        lua(f"BG3T.roll({se._lua_string(r['as'])}, {se._lua_string(r['type'])}, {se._lua_string(r['id'])}, "
            f"{int(r['dc'])}, {int(r.get('n', 20))}); return true")
    if mode == "ai":
        cst = f"BG3T.spawns[{se._lua_string(c['caster'])}]"
        keep = [c["spell"]] + list(c.get("ai_keep", []))
        native = lua(f"local g = {cst} local out = {{}} for _, s in ipairs({{{', '.join(se._lua_string(s) for s in keep)}}}) do out[s] = Osi.HasSpell(g, s) == 1 end return out") or {}
        added = [s for s in keep if not native.get(s)]
        if added:  # reactions/interrupts never fire on script-added spells (2026-10-02): say so up front
            notes.append(f"WARNING: {', '.join(added)} not native to {c['caster']} - added by script, so player/NPC "
                         "reactions and interrupts on its rolls will NOT fire; use a template that knows it (bg3_save_spells)")
        locked = lua(f"return BG3T.aiOnly({cst}, {{{', '.join(se._lua_string(s) for s in keep)}}})")
        notes.append(f"{c['caster']}: {locked} other spells on cooldown (re-applied each of its turns); AI keeps {', '.join(keep)}")
        if c.get("clear_between"):
            tgt = "BG3T.host()" if c.get("target", "host") == "host" else f"BG3T.spawns[{se._lua_string(c['target'])}]"
            lua(f"table.insert(BG3T.aiClear, {{ caster = {cst}, target = {tgt}, statuses = {{{', '.join(se._lua_string(s) for s in c['clear_between'])}}} }}); return true")
        if c.get("focus"):  # game window in front (player-side decisions; made no difference for #22 on 2026-10-02)
            from . import gameui as _g
            notes.append("game window focused" if _g.focus_game() else "couldn't focus the game window")
        if c.get("auto_reactions", True):  # reactions of the target/host on auto (Enabled, no Ask), after a tick
            time.sleep(0.6)  # a freshly added passive's interrupt preference appears a moment later
            for who in {"host", c.get("target", "host")} - {"host"}:  # the host's were set at staging (`reactions`)
                lua(f"return BG3T.autoReactions(BG3T.spawns[{se._lua_string(who)}])")
        if c.get("sanctuary", True):  # keep the enemy AI off the host so it uses the spell on the target
            lua("Osi.ApplyStatus(BG3T.host(), 'SANCTUARY', 600, 1, BG3T.host()); return true")
        time.sleep(1.5)
        c = dict(c, end_turns=int(c.get("ai_rounds", 8)))
    if mode in ("auto", "script") and c.get("spell"):
        if c.get("target") == "ground":
            lua(f"BG3T.castAt(BG3T.host(), {se._lua_string(c['spell'])}, {float(c.get('distance', 3))}); return true")
        else:
            tgt = "BG3T.host()" if c.get("target", "host") == "host" else f"BG3T.spawns[{se._lua_string(c['target'])}]"
            # caster = "A": a spawn casts the case's spell (e.g. an enemy's save effect against the host)
            who = "BG3T.host()" if c.get("caster", "host") == "host" else f"BG3T.spawns[{se._lua_string(c['caster'])}]"
            rolled = "true" if c.get("real_rolls") else "false"
            # repeat = N casts the spell N times, `repeat_wait` seconds apart (e.g. an Extra Attack chain)
            for i in range(max(1, int(c.get("repeat", 1)))):
                if i:  # event-driven: wait for the previous cast (repeat_wait is the cap), then let effects land
                    _wait_casts(since, c["spell"], i, float(c.get("repeat_wait", 3)) + 4)
                    _settle(since, quiet=0.5, cap=3)
                for st_ in c.get("clear_between", []):  # e.g. PRONE, so every cast can land it again
                    lua(f"local g = {tgt} if Osi.HasActiveStatus(g, {se._lua_string(st_)}) == 1 then Osi.RemoveStatus(g, {se._lua_string(st_)}) end return true")
                lua(f"BG3T.cast({who}, {se._lua_string(c['spell'])}, {tgt}, {rolled}); return true")
    if c.get("end_turns"):  # end the host's turn N times through the HUD (turn-order features, enemy turns)
        from . import gameui
        need = int(c.get("ai_samples", 0)) if mode == "ai" else 0
        for k in range(int(c["end_turns"])):
            if not gameui.end_host_turn():
                notes.append("end_turns: the host's turn never came back")
                break
            if need:  # ai early stop: enough matching saves recorded
                got = _ai_samples(c, since)
                if got is not None and got >= need:
                    notes.append(f"ai: stopped after {k + 1} rounds ({got} samples >= ai_samples {need})")
                    break
    return {"ok": True, "case": c, "notes": notes, "combat": combat, "first_turn": first_turn,
            "spell_name": _name(store, active, c["spell"]) if c.get("spell") else None, "before": before}


def _events(since):
    return lua(f"return BG3T.drain({since})", timeout=20) or []


def _count_casts(evs, spell):
    return sum(1 for x in evs if x.get("kind") == "CastedSpell" and x.get("spell") == spell)


def _progress(since, spell=""):
    return lua(f"return BG3T.progress({since}, {se._lua_string(spell or '')})", timeout=20) or {}


def _wait_casts(since, spell, n, timeout):
    """Event-driven wait for the n-th CastedSpell of `spell` since `since` (one small call per poll)."""
    end = time.time() + timeout
    while time.time() < end:
        if (_progress(since, spell).get("casts") or 0) >= n:
            return True
        time.sleep(0.1)
    return False


def _settle(since, quiet=0.8, cap=6.0):
    """Wait until no new events arrive for `quiet` seconds (effects after a cast: projectiles, statuses), capped."""
    end, last, t_last = time.time() + cap, -1, time.time()
    while time.time() < end:
        n = _progress(since).get("n") or 0
        if n != last:
            last, t_last = n, time.time()
        elif time.time() - t_last >= quiet:
            return True
        time.sleep(0.1)
    return False


def _ai_samples(c, since):
    """Matching saves recorded so far for the case's first `saves` expectation (ai early stop)."""
    sv = next((e["saves"] for e in c.get("expect", []) if e.get("saves")), None)
    if not sv:
        return None
    tgt = c.get("target", "host") if not sv.get("target") else sv["target"]
    evs = _events(since)
    who = lua("return BG3T.host()") if tgt == "host" else lua(f"return BG3T.spawns[{se._lua_string(tgt)}]")
    return sum(1 for x in evs if x.get("kind") == "Save" and x.get("who") == who
               and (not sv.get("ability") or x.get("ability") == sv["ability"]) and (not sv.get("spell_only") or x.get("spellcast")))


# ------------------------------------------------------------------ spell pre-checks (lessons of 2026-10-02)
_SAVE_RE = re.compile(r"SavingThrow\(\s*Ability\.(\w+)")


# what the AI actually did with a spell in mode ai (in game) - shown by spell_check / save_spells
AI_OBSERVED = {
    "Target_StrengthDrain_Shadow": "2026-10-02: Shadow_A (749b1e7d-...) casts it natively; reactions fire on its saves",
    "Target_DEN_Entangle_Staff": "2026-10-02: cast ~1 turn in 3; the target's saves came from the vine surface, not the cast",
    "Target_LOW_Poltergeist_Shove": "2026-10-02: never cast in 10 AI rounds",
    "Target_Bite_Wolf": "2026-10-02: cast every turn; its Strength saves come from the wolf's trip-on-hit passive",
}


def spell_facts(store, active, spell):
    """What a test needs to know about a spell before using it, from stats alone."""
    r = store.resolve(spell, active)
    if not r:
        return None
    f = {k: str(v[0]) for k, v in (r.get("fields") or {}).items()}
    roll = f.get("SpellRoll", "")
    return {
        "name": spell, "type": f.get("SpellType", ""), "roll": roll,
        "save_abilities": sorted(set(_SAVE_RE.findall(roll))),          # saves that are the spell's OWN roll
        "attack": "Attack(" in roll,
        "ai_can_use": "CanNotUse" not in f.get("AIFlags", ""),
        "requirements": f.get("RequirementConditions", ""),
        "target_conditions": f.get("TargetConditions", ""),
        "area": f.get("AreaRadius", "") or f.get("ExplodeRadius", ""),
        "costs": f.get("UseCosts", ""), "cooldown": f.get("Cooldown", ""),
        "other_saves": sorted(set(_SAVE_RE.findall(" ".join(f.get(k, "") for k in ("SpellSuccess", "SpellProperties", "SpellFail"))))),
    }


def spell_check(store, active, spell):
    """Readable pre-check: AI usability, where saves come from, and what will trip a test."""
    s = spell_facts(store, active, spell)
    if not s:
        return f"{spell}: not in the index"
    out = [f"{spell} ({s['type']})", f"  SpellRoll: {s['roll'] or '(none)'}"]
    if s["save_abilities"]:
        out.append(f"  own-roll save: {', '.join(s['save_abilities'])} - a real cast raises OnPostRoll interrupts for it")
    elif s["attack"]:
        out.append("  attack roll, no own save (any save on hit comes from a passive/status - interrupts never see it)")
    else:
        out.append("  no roll of its own")
    if s["other_saves"]:
        out.append(f"  saves inside its effects (status/surface ticks, not the spell's roll): {', '.join(s['other_saves'])}")
    out.append(f"  AI can cast it: {'yes' if s['ai_can_use'] else 'NO (AIFlags CanNotUse) - mode ai never sees it; use script + real_rolls'}")
    for k, label in (("requirements", "RequirementConditions"), ("target_conditions", "TargetConditions"), ("area", "area"),
                     ("costs", "UseCosts"), ("cooldown", "Cooldown")):
        if s[k]:
            out.append(f"  {label}: {s[k]}")
    if s["area"]:
        out.append("  area/surface spell: in game its saves can come from the surface or status it leaves, not the cast")
    if spell in AI_OBSERVED:
        out.append(f"  seen in mode ai: {AI_OBSERVED[spell]}")
    if "SpellSlot" in s["costs"]:
        out.append("  needs a spell slot: an NPC caster may have none (grant ActionResource(SpellSlot,...) in setup)")
    return "\n".join(out)


def save_spells(store, active, ability, limit=40):
    """AI-castable spells whose OWN roll is a saving throw of `ability` (the ones that exercise save interrupts),
    single-target first, no RequirementConditions, no spell slot."""
    rows = store.search_stats(f"SavingThrow(Ability.{ability}", active, type_="SpellData", field="SpellRoll", limit=1000)
    good = []
    for r in rows:
        n = r if isinstance(r, str) else (r.get("name") if isinstance(r, dict) else r[0])
        s = spell_facts(store, active, n)
        if not s or not s["ai_can_use"] or s["requirements"] or "SpellSlot" in s["costs"]:
            continue
        good.append(((s["type"] != "Target", bool(s["area"])), n, s))
    good.sort(key=lambda x: (x[0], x[1]))
    lines = [f"AI-castable spells with an own-roll {ability} save (no requirements, no slot): {len(good)}"]
    for _, n, s in good[:limit]:
        seen = f"  [seen: {AI_OBSERVED[n][12:60]}]" if n in AI_OBSERVED else ""
        natives = [r[1] for r in (store.references(n, active, limit=12) or []) if r[0] == "templates"][:3]
        seen += f"  native to: {', '.join(natives)}" if natives else "  (no template knows it natively)"
        lines.append(f"  {n:50s} {s['type']:10s} target: {s['target_conditions'][:50]}{'  AREA ' + s['area'] if s['area'] else ''}{seen}")
    return "\n".join(lines)


def _who(st_):
    t = st_.get("target", "host")
    return "BG3T.host()" if t == "host" else f"BG3T.spawns[{se._lua_string(t)}]"


def verify(store, active, cleanup=True, wait=2.0):
    state = _load_state()
    if not state:
        raise ValueError("nothing staged (run bg3_test_stage first)")
    c = find_case(state["layer"], state["case"])
    # event-driven: the case's casts resolved, then events went quiet for 0.8 s (`wait` stays the cap, so slow
    # projectiles/summons still get their time; fast cases finish in about a second instead of a fixed 4 s)
    if state["mode"] in ("auto", "script") and c.get("spell"):
        _wait_casts(state["since"], c["spell"], max(1, int(c.get("repeat", 1))), max(wait, 1) + 4)
        _settle(state["since"], quiet=0.8, cap=max(wait, 1.5))
    elif state["mode"] == "ai":
        _settle(state["since"], quiet=0.8, cap=3)
    else:
        time.sleep(max(0.0, min(wait, 30)))
    after = lua("return BG3T.world()", timeout=30)
    events = lua(f"return BG3T.drain({state['since']})", timeout=30) or []
    before = state["before"]
    mode = state["mode"]
    player = mode == "player"
    costs = None if player or not c.get("spell") else _use_costs(c["spell"])
    host = (before.get("host") or {}).get("guid")
    results = []

    def ent(snap, alias):
        return (snap or {}).get(alias) or {}

    def row(ok, text):
        results.append(("PASS" if ok is True else "FAIL" if ok is False else "SKIP", text))

    casts = [e for e in events if e.get("kind") == "CastedSpell" and e.get("who") == host]
    for e in c.get("expect", []):
        alias = e.get("target", "host")
        b, a = ent(before, alias), ent(after, alias)
        guid = b.get("guid")
        label = alias if alias != "host" else "you"
        if "dead" in e:
            row(a.get("dead") == e["dead"], f"{label} {'died' if e['dead'] else 'survived'} (dead={a.get('dead')})")
        if "hp_change" in e:
            lo, hi = e["hp_change"]
            d = (a.get("hp") or 0) - (b.get("hp") or 0)
            row(lo <= d <= hi, f"{label} HP change {d:+d} within [{lo}, {hi}] ({b.get('hp')} -> {a.get('hp')})")
        if "max_hp_change" in e:
            d = (a.get("max_hp") or 0) - (b.get("max_hp") or 0)
            row(d == e["max_hp_change"], f"{label} max HP {b.get('max_hp')} -> {a.get('max_hp')} (change {d:+d}, expected {e['max_hp_change']:+d})")
        if "hp" in e:
            want = a.get("max_hp") if e["hp"] == "full" else e["hp"]
            row(a.get("hp") == want, f"{label} HP {a.get('hp')}/{a.get('max_hp')} == {e['hp']}")
        for s in e.get("status_present", []):
            row(s in (a.get("statuses") or []), f"{label} has {s}")
        for s in e.get("status_absent", []):
            row(s not in (a.get("statuses") or []), f"{label} doesn't have {s}")
        for s in e.get("status_applied", []):
            hit = any(x.get("kind") == "StatusApplied" and x.get("who") == guid and x.get("status") == s for x in events)
            row(hit, f"{s} applied to {label}")
        if e.get("cast_count"):  # {spell (default the case's), by = alias, count = [lo, hi]}
            cc = e["cast_count"]
            sp = cc.get("spell", c.get("spell"))
            bg = ent(before, cc.get("by", "host")).get("guid") if cc.get("by", "host") != "host" else host
            n = sum(1 for x in events if x.get("kind") == "CastedSpell" and x.get("spell") == sp and x.get("who") == bg)
            lo, hi = cc.get("count", [1, 10 ** 6])
            row(lo <= n <= hi, f"{cc.get('by', 'host')} cast {sp} {n} times, expected [{lo}, {hi}]")
        if "amount_change" in e or "max_change" in e:  # the character's pool before/after (not a spell's cost)
            lvl = str(e.get("level", 0))
            bv = ((b.get("resources") or {}).get(e["resource"]) or {}).get(lvl) or [0, 0]
            av = ((a.get("resources") or {}).get(e["resource"]) or {}).get(lvl) or [0, 0]
            if "amount_change" in e:
                want = e["amount_change"]; d = av[0] - bv[0]
                ok = (want[0] <= d <= want[1]) if isinstance(want, list) else d == want
                row(ok, f"{label} {e['resource']}[{lvl}] amount {bv[0]:g} -> {av[0]:g} (change {d:+g}, expected {want})")
            if "max_change" in e:
                d = av[1] - bv[1]
                row(d == e["max_change"], f"{label} {e['resource']}[{lvl}] max {bv[1]:g} -> {av[1]:g} (change {d:+g}, expected {e['max_change']:+})")
        for key in ("skill", "ability"):  # { skill = "Athletics", change = 2 } / { ability = "Strength", change = 1 }
            if e.get(key):
                plural = "abilities" if key == "ability" else "skills"
                bv = ((b.get(plural) or {}).get(e[key]))
                av = ((a.get(plural) or {}).get(e[key]))
                ok = bv is not None and av is not None and av - bv == e.get("change", 0)
                row(ok, f"{label} {e[key]} {bv} -> {av} (expected change {e.get('change', 0):+})")
        for key in ("temp_hp", "temp_hp_change"):  # Temporary Hit Points after the run / gained during it
            if key in e:
                lo, hi = e[key]
                v = (a.get("temp_hp") or 0) - ((b.get("temp_hp") or 0) if key == "temp_hp_change" else 0)
                row(lo <= v <= hi, f"{label} temporary HP {'change ' if key == 'temp_hp_change' else ''}{v} within [{lo}, {hi}] "
                    f"({b.get('temp_hp')} -> {a.get('temp_hp')})")
        if e.get("damage_count"):  # {count = [lo, hi], by = alias}: separate hits that damaged `target`
            dc = e["damage_count"]
            src = ent(before, dc["by"]).get("guid") if dc.get("by") and dc["by"] != "host" else (host if dc.get("by") == "host" else None)
            n = sum(1 for x in events if x.get("kind") == "Damage" and x.get("who") == guid and (src is None or x.get("by") == src)
                    and (x.get("amount") or 0) > 0)
            lo, hi = dc.get("count", [1, 10 ** 6])
            row(lo <= n <= hi, f"{label} damaged {n} times{' by ' + dc['by'] if dc.get('by') else ''}, expected [{lo}, {hi}]")
        if e.get("cast_only"):  # alias: every spell that spawn cast during the run was the case's spell (or ai_keep)
            bg = ent(before, e["cast_only"]).get("guid")
            allowed = {c.get("spell")} | set(c.get("ai_keep", []))
            # reactions the engine triggers (opportunity attacks) aren't the AI's choice
            other = sorted({x.get("spell") for x in events if x.get("kind") == "CastedSpell" and x.get("who") == bg
                            and "OpportunityAttack" not in str(x.get("spell"))} - allowed)
            row(not other, f"{e['cast_only']} cast only {', '.join(sorted(allowed))}" + (f" (also cast: {', '.join(other)})" if other else ""))
        if e.get("saves"):  # {ability, by = alias (source), n = [lo, hi], failed = [lo, hi]} for `target`
            sv = e["saves"]
            src = ent(before, sv["by"]).get("guid") if sv.get("by") and sv["by"] != "host" else (host if sv.get("by") == "host" else None)
            rows_ = [x for x in events if x.get("kind") == "Save" and x.get("who") == guid
                     and (not sv.get("ability") or x.get("ability") == sv["ability"]) and (src is None or x.get("by") == src)
                     and (not sv.get("spell_only") or x.get("spellcast"))]
            nf = sum(1 for x in rows_ if not x.get("saved"))
            if "min_total" in sv:  # exact per-roll floor check: every save's total is at least min_total
                low = [x for x in rows_ if x.get("total", 0) < sv["min_total"]]
                row(not low and rows_ != [], f"{label}: every {sv.get('ability', '')} save total >= {sv['min_total']}"
                    + (f" - below: {', '.join(str(x.get('natural')) + '->' + str(x.get('total')) for x in low)}" if low else f" ({len(rows_)} saves)"))
            lo, hi = sv.get("n", [1, 10 ** 6]); flo, fhi = sv.get("failed", [0, 10 ** 6])
            detail = ", ".join(f"{x.get('natural')}->{x.get('total')} vs {x.get('dc')}{'' if x.get('spellcast') else ' (not a spell roll)'}" for x in rows_[:10])
            if "disadvantage" in sv:  # every matching save rolled with (or without) Disadvantage
                bad = [x for x in rows_ if bool(x.get("disadvantage")) != bool(sv["disadvantage"])]
                row(not bad and rows_ != [], f"{label}: {len(rows_) - len(bad)}/{len(rows_)} saves with Disadvantage={sv['disadvantage']}")
            if sv.get("effect_status"):  # save events show the dice BEFORE interrupts: count what actually landed
                landed = sum(1 for x in events if x.get("kind") == "StatusApplied" and x.get("who") == guid and x.get("status") == sv["effect_status"])
                row(landed <= nf and (landed == 0 if sv.get("rescued") else True) and nf - landed >= sv.get("rescued_min", 0),
                    f"{label}: {sv['effect_status']} landed {landed} times for {nf} failed rolls (rescued by interrupts: {nf - landed})")
            row(lo <= len(rows_) <= hi and flo <= nf <= fhi,
                f"{label}: {len(rows_)} {sv.get('ability', '')} saves{' vs ' + sv['by'] if sv.get('by') else ''}, {nf} failed "
                f"(expected n [{lo}, {hi}], failed [{flo}, {fhi}]) [{detail}]")
        if e.get("interrupt_used"):  # {name, count = [lo, hi]}: also reports how often it was considered
            iu = e["interrupt_used"]
            used = sum(1 for x in events if x.get("kind") == "InterruptUsed" and x.get("interrupt") == iu["name"])
            seen = sum(1 for x in events if x.get("kind") == "InterruptConsidered" and x.get("interrupt") == iu["name"])
            lo, hi = iu.get("count", [1, 10 ** 6])
            row(lo <= used <= hi, f"{iu['name']} used {used} times, expected [{lo}, {hi}] (considered {seen} times)")
        if e.get("status_applied_count"):  # {status, count = [lo, hi]}: how many times it landed during the run
            sc = e["status_applied_count"]
            n = sum(1 for x in events if x.get("kind") == "StatusApplied" and x.get("who") == guid and x.get("status") == sc["status"])
            lo, hi = sc.get("count", [1, 10 ** 6])
            row(lo <= n <= hi, f"{sc['status']} applied to {label} {n} times, expected [{lo}, {hi}]")
        if e.get("status_applied_any"):
            seen = sorted({x.get("status") for x in events if x.get("kind") == "StatusApplied" and x.get("who") == guid} & set(e["status_applied_any"]))
            row(bool(seen), f"any of {', '.join(e['status_applied_any'])} applied to {label}" + (f" (got {', '.join(seen)})" if seen else ""))
        for s in e.get("status_removed", []):
            was = s in (b.get("statuses") or [])
            row(was and s not in (a.get("statuses") or []), f"{s} removed from {label}" + ("" if was else " (it wasn't present before!)"))
        if "resource" in e and "amount_change" not in e and "max_change" not in e:  # spell-cost check
            lvl = str(e.get("level", 0))
            bv = ((b.get("resources") or {}).get(e["resource"]) or {}).get(lvl)
            av = ((a.get("resources") or {}).get(e["resource"]) or {}).get(lvl)
            if not player:
                want = -e.get("change", -1)
                charged = sum(a_ for n_, lv_, a_ in (costs or []) if _cost_matches(n_, e["resource"]) and str(lv_) == lvl)
                has = want <= 0 or (bv is not None and bv[0] >= want)  # an x0 (free) cost needs none of it
                row(charged == want and has, f"[data] {c.get('spell')} UseCosts charge {e['resource']}[{lvl}] x{charged:g} "
                    f"(expected x{want}); you had {bv[0] if bv else 'none'}{'' if has else ' - not enough to cast'}")
            elif bv is None or av is None:
                row(False, f"{label} has no {e['resource']}[{lvl}] resource")
            else:
                d = av[0] - bv[0]
                row(d == e.get("change", -1), f"{e['resource']}[{lvl}] {bv[0]:g} -> {av[0]:g} (change {d:+g}, expected {e.get('change', -1):+d})")
        if e.get("cast"):
            spell = c.get("spell") if e["cast"] is True else e["cast"]
            row(any(x.get("spell") == spell for x in casts), f"cast event for {spell} from you" + ("" if casts else " (no cast seen)"))
        if e.get("acted_first"):
            ft = state.get("first_turn")
            row(bool(ft) and ft.get("who") == host, f"you acted first in initiative" + ("" if ft else " (no turn recorded)"))
        if e.get("consecutive_turns") or e.get("took_turn"):
            # turns started after the case's cast, in order (aliases; None = an untracked creature)
            i0 = next((i for i, x in enumerate(events) if x.get("kind") == "CastedSpell" and x.get("who") == host
                       and x.get("spell") == c.get("spell")), None)
            # creatures' turns only: one started while incapacitated is skipped by the engine; items' turns are scenery
            seq = [x.get("tracked") or f"other:{x.get('name') or x.get('who')}" for x in events[(i0 or 0):]
                   if x.get("kind") == "TurnStarted" and not x.get("incap") and not x.get("item")]
            if e.get("consecutive_turns"):
                n = 0
                while n < len(seq) and seq[n] == e["consecutive_turns"]:
                    n += 1
                lo, hi = e.get("count", [1, 10 ** 6])
                row(i0 is not None and lo <= n <= hi, f"{e['consecutive_turns']} took {n} turns in a row after the cast, "
                    f"expected [{lo}, {hi}] (order: {seq[:12]})")
            if e.get("took_turn"):
                row(e["took_turn"] in seq, f"{e['took_turn']} took a turn after the cast")
        if e.get("damage_type"):
            hits = [x for x in events if x.get("kind") == "Damage" and x.get("who") == guid]
            row(any(x.get("type") == e["damage_type"] for x in hits), f"{label} took {e['damage_type']} damage (seen: {sorted({x.get('type') for x in hits}) or 'none'})")
    if any("roll" in e for e in c.get("expect", [])):
        tallies = lua("return BG3T.rolls", timeout=30) or {}
        for e in c.get("expect", []):
            if "roll" in e:
                t_ = tallies.get(e["roll"]) or {}
                lo, hi = e.get("pass", [0, 10 ** 6])
                got, n = t_.get("pass", 0), t_.get("n", 0)
                done = got + t_.get("fail", 0)
                row(lo <= got <= hi and done == n, f"roll {e['roll']}: {got}/{n} passed, expected [{lo}, {hi}]"
                    + ("" if done == n else f" (only {done} results came back)"))
    safety = [x for x in events if x.get("kind") == "SAFETY"]
    where = " in real combat (initiative)" if state["combat"] else " out of combat"
    if player:
        fid = "player cast" + where
    elif mode == "ai":
        fid = f"AI cast by {c.get('caster')} on its own turns - real rolls and interrupts"
    elif mode == "auto":
        fid = "scripted cast" + where + "; costs checked against the loaded UseCosts, not charged"
    else:
        fid = "SCRIPTED cast - " + ("saves rolled for real" if c.get("real_rolls") else "effects only, saves not rolled")
    npass = sum(1 for r in results if r[0] == "PASS")
    nfail = sum(1 for r in results if r[0] == "FAIL")
    verdict = "FAIL" if nfail else ("PASS" if npass else "NO CHECKS")
    if safety:
        verdict = "ABORTED (safety watch)" if not nfail else verdict
    lines = [f"{c['id']}: {verdict}  [{fid}]", f"  {c.get('title', '')}"]
    lines += [f"  {s} {t}" for s, t in results]
    if safety:
        lines.append(f"  SAFETY: party HP fell below {lua('return BG3T.safety.floor')}% - spawns killed, you were healed")
    if player and not casts and c.get("spell"):
        lines.append("  note: no cast from you was recorded - did the spell go off?")
    ev = [f"{x['kind']}" + (f" {x.get('spell') or x.get('status') or x.get('type') or ''}" if x['kind'] != 'TurnStarted' else f" {x.get('tracked') or 'other'}")
          + (f" {x.get('amount')}" if x.get('amount') else "") for x in events][:30]
    if ev:
        lines.append("  events: " + ", ".join(ev))
    if cleanup:
        # statuses the test run left on you (e.g. a scripted Mage Armour) must not end up in a save
        pre = state.get("pre_statuses") or ent(before, "host").get("statuses") or []
        # never strip downed/dying states: removing DOWNED leaves the host alive at 0 HP with no way back (2026-10-02)
        gained = [x for x in (ent(after, "host").get("statuses") or []) if x not in pre
                  and "DOWNED" not in x and "DYING" not in x and x not in ("UNCONSCIOUS", "KNOCKED_OUT")]
        if gained:
            lua("local h=BG3T.host(); " + " ".join(f"pcall(Osi.RemoveStatus,h,{se._lua_string(x)});" for x in gained) + " return true")
            lines.append(f"  removed from you: {', '.join(gained)}")
        rep = lua("return BG3T.cleanup()")
        # back on your feet: a run that downed the host (e.g. a Last Stand test) ends with them up at full HP
        lua("Osi.SetHitpointsPercentage(BG3T.host(), 100) return true")
        lines.append(f"  cleanup: {rep.get('spawns', 0)} spawns, {rep.get('grants', 0)} boosts, {rep.get('statuses', 0)} statuses, {rep.get('passives', 0)} passives, {rep.get('cooldowns', 0)} cooldowns removed")
        try:
            os.remove(STATE_FILE)
        except OSError:
            pass
    return "\n".join(lines)


def _use_costs(spell):
    """[(resource, level, amount)] from the spell's UseCosts as the running game loaded them."""
    raw = lua(f"local s=Ext.Stats.Get({se._lua_string(spell)}); return s and tostring(s.UseCosts) or ''") or ""
    out = []
    for part in filter(None, (p.strip() for p in raw.split(";"))):
        f = part.split(":")
        if f[0] == "SpellSlotsGroup" and len(f) >= 4:   # SpellSlotsGroup:group:amount:level
            out.append(("SpellSlotsGroup", int(f[3]), float(f[2])))
        elif len(f) >= 2:                               # Name:amount[:level]
            out.append((f[0], int(f[2]) if len(f) > 2 else 0, float(f[1])))
    return out


def _cost_matches(cost_name, resource):
    # SpellSlotsGroup is paid from SpellSlot or WarlockSpellSlot of that level
    return cost_name == resource or (cost_name == "SpellSlotsGroup" and resource in ("SpellSlot", "WarlockSpellSlot"))


def run_console(c):
    """A mod's own test command (e.g. '!apofeature X'): pass when `expect_log` appears in its output."""
    lines = se.command(c["console"], wait=float(c.get("wait", 8)))
    text = "\n".join(lines)
    ok = c["expect_log"] in text
    bad = c.get("fail_log") and c["fail_log"] in text
    verdict = "PASS" if ok and not bad else "FAIL"
    tail = [l for l in lines if l.strip() and "Switching to" not in l][-6:]
    return f"{c['id']}: {verdict}  [mod console command: {c['console']}]\n  {c.get('title', '')}\n" + "\n".join(f"  | {l}" for l in tail)


def run(store, active, layer, case_id, wait=4.0):
    """Stage + (scripted) cast + verify in one go for auto/script cases; console cases run their command.
    `retries` re-runs a failed case (for save-based effects the target can resist)."""
    c = find_case(layer, case_id)
    if c.get("console"):
        return run_console(c)
    if c.get("mode", "auto") == "player":
        raise ValueError(f"{case_id} is a player-mode case: use bg3_test_stage, cast from the hotbar, then bg3_test_verify")
    out = ""
    for attempt in range(1 + int(c.get("retries", 0))):
        r = stage(store, active, layer, case_id)
        if not r["ok"]:
            return f"{case_id}: NOT RUN - " + "; ".join(r["blockers"])
        out = verify(store, active, cleanup=True, wait=wait)
        if ": PASS" in out.split("\n")[0]:
            return out + (f"\n  (passed on attempt {attempt + 1})" if attempt else "")
    return out


def run_level(store, active, layer, cls, level, wait=4.0, build=None):
    if build:
        b = find_build(layer, build)
        cls = b["class"]
        cases = [c for c in assign_cases(load_cases(layer), load_builds(layer)).get(build, []) if c.get("level") == level]
    else:
        cases = [c for c in load_cases(layer) if (c.get("class") or "").lower() == (cls or "").lower() and c.get("level") == level]
    if not cases:
        return f"no {cls} level {level} cases" + (f" for build {build}" if build else "")
    out, manual = [], []
    for c in cases:
        if c.get("mode", "auto") == "player":
            manual.append(c["id"])
            continue
        try:
            out.append(run(store, active, layer, c["id"], wait))
        except (RuntimeError, ValueError, TimeoutError) as e:
            out.append(f"{c['id']}: ERROR {e}")
            try:
                cleanup()
            except (RuntimeError, TimeoutError):
                pass
    passed = sum(1 for o in out if o.split("\n")[0].endswith("]") and ": PASS" in o.split("\n")[0])
    head = f"{cls} level {level}: {passed}/{len(out)} automated cases passed" + (f"; player-mode (run by hand): {', '.join(manual)}" if manual else "")
    return head + "\n\n" + "\n\n".join(out)


def cleanup():
    rep = lua("return BG3T.cleanup()")
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass
    return rep


# ------------------------------------------------------------------ progression lint
GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def lint_progressions(store, active, layer):
    """Static checks on the progression nodes a layer defines: invalid node UUIDs (the game drops the
    node), selectors pointing at lists no layer defines, and several nodes for the same table+level from
    different layers (both load: choices and feats are granted twice)."""
    w, p = store._where(active)
    rows = store.db.execute(f"SELECT layer, name, level, table_uuid, uuid, attrs FROM prog WHERE {w} ORDER BY rank", p).fetchall()
    mine = [r for r in rows if r[0] == layer]
    out = []
    bad = [r for r in mine if r[4] and not GUID_RE.match(r[4])]
    if bad:
        out.append(f"INVALID node UUIDs ({len(bad)}) - the game doesn't load these nodes:")
        out += [f"  {r[1]} L{r[2]}: {r[4]}" for r in bad]
    dangling = []
    for r in mine:
        a = json.loads(r[5])
        for kind, args in re.findall(r"(\w+)\(([^)]*)\)", a.get("Selectors") or ""):
            lst = args.split(",")[0].strip()
            if kind in ("SelectSpells", "AddSpells", "SelectPassives") and GUID_RE.match(lst) and not store.spell_list(lst, active):
                dangling.append(f"  {r[1]} L{r[2]}: {kind}({lst}) - list not defined in {'+'.join(active)}")
    if dangling:
        out.append(f"DANGLING list references ({len(dangling)}):")
        out += sorted(set(dangling))
    by = {}
    for r in rows:
        a = json.loads(r[5])
        if str(a.get("IsMulticlass", "")).lower() == "true":
            continue
        by.setdefault((r[3], r[2]), []).append(r)
    def grants_choice(x):
        at = json.loads(x[5])
        return at.get("AllowImprovement") == "true" or bool(at.get("Selectors"))

    # only stacked CHOICES/feats are harmful: an extra node that just adds passives is a normal way to extend
    dup = []
    for k, v in by.items():
        latest = {}
        for x in v:  # same node UUID in several layers = one node (last layer wins)
            latest[x[4]] = x
        nodes = list(latest.values())
        if len(nodes) > 1 and any(x[0] == layer for x in nodes) and sum(grants_choice(x) for x in nodes) > 1:
            dup.append((k, nodes))
    if dup:
        out.append(f"STACKED choices ({len(dup)}): several nodes for one table+level each grant choices/feats - all load, so they're offered twice:")
        for (t, l), v in sorted(dup, key=lambda d: (d[1][0][1], d[0][1])):
            out.append(f"  {v[0][1]} L{l}: " + "; ".join(f"{x[0]} {x[4]}" + (" [AllowImprovement]" if json.loads(x[5]).get("AllowImprovement") == "true" else "")
                                                      + (" [Selectors]" if json.loads(x[5]).get("Selectors") else "") for x in v))
    return "\n".join([f"progression lint for {layer}: " + ("clean" if not out else f"{len(bad)} invalid UUIDs, {len(set(dangling))} dangling lists, {len(dup)} stacked-choice levels")] + out)


# ------------------------------------------------------------------ build plans
MAIN_ABILITY = {"wizard": "Intelligence", "artificer": "Intelligence", "sorcerer": "Charisma", "warlock": "Charisma",
                "bard": "Charisma", "paladin": "Charisma", "cleric": "Wisdom", "druid": "Wisdom", "ranger": "Wisdom",
                "monk": "Wisdom", "fighter": "Strength", "barbarian": "Strength", "rogue": "Dexterity"}


def _class_names(uuids, store=None, active=None):
    """ClassDescription UUID -> (Name, display name): from the index (static data), else the running game."""
    if not uuids:
        return {}
    if store is not None:
        out = {}
        for u in uuids:
            r = store.static("ClassDescription", u, active)
            if r:
                dn = r[2].get("DisplayName") or ""
                row = store.loca(dn, active) if dn else None  # (text, source, version)
                out[u] = (r[2].get("Name"), row[0] if row else r[2].get("Name"))
        if len(out) == len(uuids):
            return out
    try:
        lst = "{" + ",".join(se._lua_string(u) for u in uuids) + "}"
        r = se.eval_lua(f"local o={{}}; for _,u in ipairs({lst}) do local c=Ext.StaticData.Get(u,'ClassDescription'); "
                        "if c then o[u]={c.Name, Ext.Loca.GetTranslatedString(c.DisplayName.Handle.Handle)} end end; return o",
                        "server", timeout=15)
        return {k: tuple(v) for k, v in (r["result"] or {}).items()} if r["ok"] else {}
    except (RuntimeError, TimeoutError):
        return {}


def _nodes(store, active, table_name, level):
    return [n for n in store.progression(table_name, active, level) if str(n[4].get("IsMulticlass", "")).lower() != "true"]


def plan(store, active, layer, build_id, write=True, _picked_only=False):
    """Exact level-up choices per level for a build, driven by the tests assigned to it."""
    b = find_build(layer, build_id)
    cls, sub = b["class"], b.get("subclass")
    lo, hi = b["levels"]
    ability = b.get("ability") or MAIN_ABILITY.get(cls.lower(), "your main ability")
    cases = assign_cases(load_cases(layer), load_builds(layer)).get(build_id, [])
    # spells the tests need, learned no later than their test level (auto-granted ones excluded below)
    need = {}
    for c in sorted(cases, key=lambda c: c.get("level", 0)):
        if c.get("spell") and c.get("mode", "auto") != "script":
            need.setdefault(c["spell"], c.get("level", 0))
    for L, pins in (b.get("spells") or {}).items():
        for sp in pins:
            need.setdefault(sp, int(L))
    granted, picked, warnings = set(), {}, []
    if b.get("from"):  # spells the parent build already picked aren't offered again
        picked.update(plan(store, active, layer, b["from"], write=False, _picked_only=True))
    sub_uuid_names = {}
    md = [f"# Test plan: {b.get('title') or build_id}", "",
          f"Build `{build_id}`: {cls}" + (f" / {sub}" if sub else "") + f", levels {lo}-{hi}. "
          f"Generated {time.strftime('%Y-%m-%d %H:%M %Z')} (layers: {', '.join(active)}).", ""]
    if b.get("from"):
        md += [f"**Start:** load your save from build `{b['from']}`" + (f" (\"{find_build(layer, b['from']).get('save_as')}\")" if find_build(layer, b['from']).get('save_as') else "") + ".", ""]
    md += ["**Each level:** say **\"level up\"** -> make exactly the choices below -> say **\"leveled\"**. I run the level "
           "check and this level's automated tests. Save after each level (never while a test is staged).", ""]
    # levels below the plan's range still grant spells: track them so they aren't picked twice
    max_slot = 0  # highest spell-slot level so far: filler picks stay castable
    for L in range(1, hi + 1):
        nodes = _nodes(store, active, cls, L) + (_nodes(store, active, sub, L) if sub else [])
        for n_ in nodes:
            for rname, rlvl, _amt in _boost_resources(n_[4].get("Boosts")):
                if rname in ("SpellSlot", "WarlockSpellSlot"):
                    max_slot = max(max_slot, rlvl)
        body = []
        if len({n[3] for n in nodes if n[2]}) > 1 and len([n for n in nodes if n[1] == cls]) > 1:
            body.append(f"- note: {len([n for n in nodes if n[1] == cls])} {cls} progression nodes at this level "
                        f"({', '.join(sorted({n[3] for n in nodes if n[1] == cls}))}); the level-up screen may ask twice")
        for lvl, pname, table, src, a in nodes:
            if a.get("_SubClasses") and L >= lo:
                ids = [x for x in a["_SubClasses"].split(";") if x]
                sub_uuid_names = _class_names(ids, store, active)
                disp = next((v[1] for v in sub_uuid_names.values() if v[0] == sub), sub) if sub else None
                body.append(f"- **Subclass: {disp}**" + (f" (`{sub}`)" if sub and disp != sub else "") if sub else
                            f"- Subclass: not chosen in this build (stop before level {L}, or pick any)")
            for kind, args in re.findall(r"(\w+)\(([^)]*)\)", a.get("Selectors") or ""):
                args = [x.strip() for x in args.split(",")]
                if kind == "AddSpells" and args[0]:
                    granted |= set(_list_spells(store, active, args[0]) or [])
                    continue
                if L < lo:
                    if kind == "SelectSpells":  # earlier builds' picks aren't known; nothing to plan
                        pass
                    continue
                if kind == "SelectSpells":
                    n = int(float(args[1])) if len(args) > 1 and args[1] else 1
                    pool = [x for x in (_list_spells(store, active, args[0]) or []) if x not in picked and x not in granted]
                    if _list_spells(store, active, args[0]) is None:
                        body.append(f"- Spells ({args[3] if len(args) > 3 else 'list'}, pick {args[1]}): **list {args[0]} doesn't exist in any layer** - the screen will offer nothing")
                        warnings.append(f"L{L}: SelectSpells list {args[0]} is not defined anywhere")
                        continue
                    lvl_names = {x: _name(store, active, x) for x in pool}
                    lvls = sorted({str(store.resolve(x, active)["fields"].get("Level", ("?",))[0]) for x in pool if store.resolve(x, active)})
                    spell_lvl = lvls[0] if len(lvls) == 1 else (f"{lvls[0]}-{lvls[-1]}" if lvls else "?")
                    req = [x for x in sorted(need, key=need.get) if x in pool][:n]
                    later = set(need)
                    def castable(x):
                        r_ = store.resolve(x, active)
                        try:
                            return int(r_["fields"].get("Level", ("0",))[0]) <= max_slot if r_ else False
                        except ValueError:
                            return True
                    filler = [x for x in sorted(pool, key=lambda x: lvl_names[x]) if x not in req and x not in later and castable(x)][: n - len(req)]
                    for x in req + filler:
                        picked[x] = L
                    items = [f"**{lvl_names[x]}** (tested at L{need[x]})" for x in req] + [f"{lvl_names[x]} (filler)" for x in filler]
                    what = "cantrips" if spell_lvl == "0" else f"level {spell_lvl} spells"
                    extra = f", {args[3]}" if len(args) > 3 and args[3] else ""
                    body.append(f"- Spells ({what}{extra}, pick {n}): " + ", ".join(items))
                elif kind == "SelectPassives":
                    n = int(float(args[1])) if len(args) > 1 and args[1] else 1
                    row = store.spell_list(args[0], active)
                    if not row:
                        body.append(f"- Choose {n} ({args[2] if len(args) > 2 else 'passive'}): **list {args[0]} doesn't exist in any layer** - nothing to choose")
                        warnings.append(f"L{L}: SelectPassives list {args[0]} is not defined anywhere")
                        continue
                    opts = [x for x in re.split(r"[;,]", json.loads(row[4]).get("Passives", "")) if x]
                    want = [x for x in (b.get("passives") or {}).get(str(L), []) if x in opts] or opts[:n]
                    body.append(f"- Choose {n} ({args[2] if len(args) > 2 else 'passive'}): " + ", ".join(f"**{_name(store, active, x)}**" for x in want)
                                + (f" (options: {len(opts)})" if opts else ""))
                elif kind in ("SelectSkills", "SelectSkillsExpertise", "SelectAbilityBonus", "SelectAbilities", "SelectEquipment"):
                    body.append(f"- {kind.replace('Select', '')}: any (doesn't affect tests)")
                else:
                    body.append(f"- {kind}({', '.join(args[:2])}): any")
            if a.get("AllowImprovement") == "true" and L >= lo:
                body.append(f"- Feat: **{(b.get('feats') or {}).get(str(L), f'Ability Score Improvement, +2 {ability}')}**")
        if L < lo:
            continue
        if _picked_only:
            continue
        here = [c for c in cases if c.get("level") == L]
        md += [f"## Level {L}", ""]
        md += ["Level-up screen:" if L > 1 else "Character creation:"] + (body or ["- no choices"]) + [""]
        if here:
            auto = [c for c in here if c.get("mode", "auto") != "player"]
            man = [c for c in here if c.get("mode", "auto") == "player"]
            if auto:
                md += [f"Automated tests (`bg3_test_run_level(build=\"{build_id}\", level={L})`), nothing for you to do:"]
                md += [f"- `{c['id']}` {c.get('title', '')}" for c in auto] + [""]
            for c in man:
                md += [f"Your spot check `{c['id']}`: {c.get('title', '')}",
                       f"1. Say **\"stage {c['id']}\"**; I set it up (you act first).",
                       f"2. Cast **{_name(store, active, c['spell'])}** from the class spell bar at "
                       f"{'yourself' if c.get('target', 'host') == 'host' else _spawn_label(c, c['target'])}." + (f" {c['instructions']}" if c.get("instructions") else ""),
                       "3. Say **\"verify\"**.", ""]
        if b.get("save_as") and L == hi:
            md += [f"**Save now as \"{b['save_as']}\"**: other builds start from it.", ""]
    if _picked_only:
        return picked
    for sp, L in need.items():
        if sp not in picked and sp not in granted:
            warnings.append(f"{sp} (tested at L{L}) is never offered by a spell choice in this build")
        elif picked.get(sp, 0) > L:
            warnings.append(f"{sp} is picked at L{picked[sp]} but tested at L{L}")
    if warnings:
        md += ["## Plan warnings", ""] + [f"- {w}" for w in warnings] + [""]
    text = "\n".join(md)
    path = None
    if write:
        d = os.path.join(mod_entry(layer)["path"], "docs", "test-scripts")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"plan-{build_id}.md")
        open(path, "w", encoding="utf-8").write(text)
    return text, path


# ------------------------------------------------------------------ human test scripts
def _verb(who, third, base):
    return base if who == "you" else third


def _expect_text(store, active, c, e):
    who = "you" if e.get("target", "host") == "host" else _spawn_label(c, e["target"])
    v = lambda third, base: f"{who} {_verb(who, third, base)}"
    out = []
    if "dead" in e:
        out.append(v("dies", "die") if e["dead"] else v("survives", "survive"))
    if "hp_change" in e:
        lo, hi = e["hp_change"]
        out.append(f"{v('loses', 'lose')} {abs(hi)}-{abs(lo)} HP" if hi <= 0 else f"{v('gains', 'gain')} {lo}-{hi} HP" if lo >= 0 else f"{who} HP changes by {lo}..{hi}")
    if e.get("hp") == "full":
        out.append(f"{v('ends', 'end')} at full HP")
    for k, verb in (("status_present", ("has", "have")), ("status_absent", ("no longer has", "no longer have")),
                    ("status_applied", ("gains", "gain")), ("status_removed", ("loses", "lose"))):
        for s in e.get(k, []):
            out.append(f"{v(*verb)} {_name(store, active, s)}")
    if "resource" in e:
        lvl = e.get("level", 0)
        out.append(f"one {e['resource']}" + (f" (level {lvl})" if lvl else "") + f" is spent" if e.get("change", -1) == -1 else f"{e['resource']} changes by {e.get('change')}")
    if e.get("acted_first"):
        out.append("you act first in initiative")
    if e.get("consecutive_turns"):
        lo, hi = e.get("count", [1, 10 ** 6])
        out.append(f"{e['consecutive_turns']} takes {lo}-{hi} turns in a row")
    if e.get("took_turn"):
        out.append(f"{e['took_turn']} gets a turn again")
    if e.get("damage_type"):
        out.append(f"{v('takes', 'take')} {e['damage_type']} damage")
    if e.get("note"):
        out.append(e["note"])
    return out


def _spawn_label(c, alias):
    for s in c.get("spawn", []):
        if s.get("as") == alias:
            base = TEMPLATES.get(s["template"], (None, s["template"]))[1]
            return f"the {base} ({alias}{', ' + str(s['hp']) + ' HP' if s.get('hp') else ''})"
    return alias


def script(store, active, layer, cls=None, level=None, write=True):
    cases = [c for c in load_cases(layer) if (not cls or (c.get("class") or "").lower() == cls.lower())
             and (level is None or c.get("level") == level)]
    if not cases:
        return f"no cases for class={cls} level={level} in {suite_dirs(layer)}", None
    by_level = {}
    for c in cases:
        by_level.setdefault(c.get("level", 0), []).append(c)
    title = f"{cls or 'All classes'} test script" + (f" - level {level}" if level is not None else "")
    md = [f"# {title}", "",
          f"Generated {time.strftime('%Y-%m-%d %H:%M %Z')} from `{', '.join(suite_dirs(layer))}` (layers: {', '.join(active)}).",
          "Say the words in **bold quotes** to me; I run the tools. Never save while a test is staged.", ""]
    for lvl in sorted(by_level):
        md += [f"## Level {lvl}", ""]
        if lvl > 1:
            md += ["### Level up", "",
                   f"1. Say **\"level up\"**. I grant exactly enough XP for level {lvl} (`bg3_level_up`).",
                   "2. Open the level-up screen and make your choices (the check lists them).",
                   "3. Say **\"leveled\"**. I run `bg3_level_check`: every feature, spell and resource this level should grant.", ""]
        else:
            md += ["Say **\"level check\"** first: I confirm your level 1 features and spells (`bg3_level_check`).", ""]
        for i, c in enumerate(by_level[lvl], 1):
            spell = _name(store, active, c["spell"]) if c.get("spell") else None
            mode = c.get("mode", "auto")
            md += [f"### {lvl}.{i} {c.get('title', c['id'])}", "", f"Case `{c['id']}` - " + {"player": "you cast it from the hotbar (verifies the engine charging costs)",
                                    "auto": "automated: I set up, cast and check it; costs checked against the loaded data",
                                    "script": "I cast it by script (effects only)"}[mode] + ".", ""]
            steps = [f"Say **\"stage {c['id']}\"**. I set up:"]
            setup = []
            for s in c.get("spawn", []):
                setup.append(f"{'a hostile' if s.get('faction', 'hostile') == 'hostile' else 'a ' + s.get('faction')} "
                             f"{_spawn_label(c, s['as'])[4:]} about {s.get('distance', 8)} m away")
            for st_ in c.get("setup", []):
                who = "your" if st_.get("target", "host") == "host" else _spawn_label(c, st_["target"]) + "'s"
                if "hp" in st_:
                    setup.append(f"{who} HP set to {st_['hp']}")
                if st_.get("status"):
                    setup.append(f"{_name(store, active, st_['status'])} on {'you' if who == 'your' else who[:-2]}")
                if st_.get("boost"):
                    setup.append(f"test boost `{st_['boost']}`")
            hostile = any(s.get("faction", "hostile") == "hostile" for s in c.get("spawn", []))
            combat = c.get("combat", hostile)
            if combat:
                setup.append("combat starts; a temporary Initiative +50 makes you act first")
            steps += [f"   - {x}" for x in setup] or ["   - nothing extra"]
            if mode == "player" and spell:
                tgt = "yourself" if c.get("target", "host") == "host" else _spawn_label(c, c["target"])
                if c.get("prep"):
                    steps.append(f"Before casting: {c['prep']}")
                steps.append(f"{'On your turn, cast' if combat else 'Cast'} **{spell}** from your class spell bar at **{tgt}**. Use the hotbar, not the console.")
                if c.get("instructions"):
                    steps.append(c["instructions"])
                steps.append("Wait for the spell to resolve." + (" Don't end your turn." if combat else ""))
            else:
                steps = [f"Say **\"run {c['id']}\"** (or **\"run level {lvl}\"** for all of them). I set up:"] + steps[1:]
                steps.append("Watch: I cast it for you, check the results and clean up.")
            if mode == "player":
                steps.append("Say **\"verify\"**. I check the results, then remove every spawn and test boost.")
            md += [f"{n}. {s}" if not s.startswith("   ") else s for n, s in _number(steps)]
            exp = [t for e in c.get("expect", []) for t in _expect_text(store, active, c, e)]
            if exp:
                md += ["", "What you should see:"] + [f"- {t}" for t in exp]
            if c.get("notes"):
                md += ["", f"Notes: {c['notes']}"]
            md.append("")
    text = "\n".join(md)
    path = None
    if write:
        m = mod_entry(layer)
        d = os.path.join(m["path"], "docs", "test-scripts")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{(cls or 'all').replace(' ', '_')}{'' if level is None else f'_L{level}'}.md")
        open(path, "w", encoding="utf-8").write(text)
    return text, path


def _number(steps):
    n = 0
    for s in steps:
        if s.startswith("   "):
            yield None, s
        else:
            n += 1
            yield n, s


# ------------------------------------------------------------------ game lifecycle
def _tasklist():
    from . import platform
    return platform.tasklist()


def kill_game(graceful=True):
    """Stop the game: a clean Quit through its own UI first (a killed load makes the next launch start in a
    no-mods safe mode), taskkill if that doesn't work (hung game, no Script Extender)."""
    _, g = game_cfg()
    killed = []
    running = [p for p in g["processes"] if p.lower() in _tasklist()]
    if graceful and running:
        from . import gameui, platform
        if not platform.not_responding() and gameui.quit_game(g["processes"], _tasklist):
            return [p + " (quit cleanly)" for p in running]
    for p in g["processes"]:
        if p.lower() in _tasklist():
            from . import platform
            platform.run_win(["taskkill.exe", "/IM", p, "/F"], timeout=30)
            killed.append(p)
    for _ in range(30):
        if not any(p.lower() in _tasklist() for p in g["processes"]):
            break
        time.sleep(1)
    return killed


def _unstick_menu(log, relaunched):
    """The game is up but no save is loaded: dismiss the splash, continue from the main menu, and get out of the
    no-mods safe mode (clean quit + relaunch, once) that follows a killed or hung load."""
    from . import deploy, gameui, sources as src
    ui = gameui.screen()
    if not ui:
        return None
    if "SplashScreen" in ui:
        _unstick_menu.splash = getattr(_unstick_menu, "splash", 0) + 1
        if _unstick_menu.splash > 5 and not relaunched:  # the key isn't landing (~40 s of presses): clean quit + relaunch, once
            log.append("splash screen: still up after 5 presses - quitting cleanly to relaunch")
            _unstick_menu.splash = 0
            _, g = game_cfg()
            if gameui.quit_game(g["processes"], _tasklist):
                return "relaunch"
            kill_game(graceful=False)  # the splash may not take the menu's Quit; safe mode after this is handled
            log.append("splash screen: no clean quit available - killed the game")
            return "relaunch_killed"
        ok = gameui.press_key()
        log.append("splash screen: pressed Enter" + ("" if ok else " (no game window found)"))
        return "key"
    _unstick_menu.splash = 0
    if "MainMenu" in ui and "Dialog_box" not in ui:
        loaded = set(gameui.loaded_modules() or [])
        want = {}
        for m in src.load_config()["mods"]:
            try:
                want[m["name"]] = deploy.mod_info(m["name"])[2]["UUID"]
            except (ValueError, OSError):
                pass
        missing = [n for n, u in want.items() if u and u not in loaded]
        if missing and loaded:
            if relaunched:
                log.append(f"main menu without mods {', '.join(missing)} again - check the in-game mod manager")
                return None
            log.append(f"main menu in no-mods safe mode (not loaded: {', '.join(missing)}): quitting cleanly to relaunch")
            _, g = game_cfg()
            return "relaunch" if gameui.quit_game(g["processes"], _tasklist) else None
        gameui.main_menu_command("ContinueGameCommand")
        log.append("main menu: pressed Continue")
        return "continue"
    if "Dialog_box" in ui:
        log.append(f"a message box is open ({', '.join(ui)}) - not answering it automatically")
    return None


def newest_save():
    _, g = game_cfg()
    saves = [d for d in glob.glob(os.path.join(g["savegames"], "*")) if os.path.isdir(d)]
    if not saves:
        return None
    s = max(saves, key=os.path.getmtime)
    return os.path.basename(s), sources.iso(os.path.getmtime(s))


_RESTART_LOCK = __import__("threading").Lock()


def restart(deploy_layer=None, launch=True, timeout=300):
    # one restart at a time: a second one (e.g. after the first was backgrounded by the client) kills the game the
    # first is waiting on and both misreport (seen 2026-10-02)
    if not _RESTART_LOCK.acquire(blocking=False):
        return "another bg3_game_restart is still running in this server - wait for it (or stop it) first"
    try:
        return _restart(deploy_layer, launch, timeout)
    finally:
        _RESTART_LOCK.release()


def _restart(deploy_layer=None, launch=True, timeout=300):
    log = []
    killed = kill_game()
    log.append("killed: " + (", ".join(killed) or "game wasn't running"))
    if deploy_layer:
        from . import deploy, platform
        m = mod_entry(deploy_layer)
        cmd = m.get("deploy")
        if cmd:  # the mod's own deploy command (run in its folder)
            r = subprocess.run(platform.shell_command(cmd), cwd=m["path"], capture_output=True, text=True, timeout=600)
            tail = (r.stdout + r.stderr).strip().splitlines()[-8:]
            log.append(f"deploy ({cmd}) exit {r.returncode}:\n    " + "\n    ".join(tail))
            if r.returncode != 0:
                return "\n".join(log + ["deploy failed - not launching"])
        else:  # built in: pack, deploy, enable
            lines, ok = deploy.deploy(deploy_layer)
            log += [f"deploy: {l}" for l in lines]
            if not ok:
                return "\n".join(log + ["deploy failed - not launching"])
    if not launch:
        return "\n".join(log)
    ns = newest_save()
    log.append(f"newest save (what -continueGame loads): {ns[0]} ({ns[1]})" if ns else "no saves found")
    from . import platform
    _, g = game_cfg()
    args = " ".join(g["launch_args"])
    if g["launcher"] == "steam" and g.get("steam_exe"):
        platform.launch_detached(g["steam_exe"], ["-applaunch", str(g["app_id"])] + g["launch_args"])
        log.append(f"launched via Steam -applaunch {g['app_id']} {args}")
    elif g.get("game_exe"):
        platform.launch_detached(g["game_exe"], g["launch_args"])
        log.append(f"launched {g['game_exe']} {args}")
    else:
        return "\n".join(log + ["no launcher: set game.launcher / game.game_exe in layers.json"])
    t0 = time.time()
    last_ui, relaunched, hung_since = 0, False, None
    _unstick_menu.splash = 0
    while time.time() - t0 < timeout:
        time.sleep(5)
        try:
            r = se.eval_lua("return Osi.GetHostCharacter() and Osi.GetRegion(Osi.GetHostCharacter()) or ''", "server", timeout=8)
            if r["ok"] and r["result"]:
                log.append(f"session loaded after {time.time() - t0:.0f}s: host in {r['result']}")
                return "\n".join(log)
        except (RuntimeError, TimeoutError):
            pass
        # no session yet: see what the game shows. Check early and often: the first launch after quitting a loaded
        # game always comes up in no-mods safe mode (5/5 restarts on 2026-10-01, with or without a deploy; a quit
        # from the main menu doesn't do this), so the relaunch below is the normal path, not a rare fallback.
        if time.time() - t0 > 10 and time.time() - last_ui > 8:
            last_ui = time.time()
            # a load that hangs (seen 2026-10-02: a bad stats build froze at LoadModule, CPU flat) never recovers:
            # report it after ~60 s of Not Responding instead of waiting out the whole timeout
            hung_since = (hung_since or time.time()) if platform.not_responding() else None
            if hung_since and time.time() - hung_since > 60:
                log.append(f"HUNG: the game window has been Not Responding for {time.time() - hung_since:.0f}s "
                           f"({time.time() - t0:.0f}s after launch), a hung module/save load. Suspect the last deploy "
                           "(stats/.khn/Lua); bisect it. The game was left running - restart kills it.")
                return "\n".join(log)
            note = _unstick_menu(log, relaunched)
            if note in ("relaunch", "relaunch_killed"):
                relaunched = note == "relaunch"  # after a kill, the safe-mode relaunch must still be allowed
                if g["launcher"] == "steam" and g.get("steam_exe"):
                    platform.launch_detached(g["steam_exe"], ["-applaunch", str(g["app_id"])] + g["launch_args"])
                elif g.get("game_exe"):
                    platform.launch_detached(g["game_exe"], g["launch_args"])
                log.append("relaunched after a clean quit")
                t0 = time.time()
    state = ""
    hung = platform.not_responding()
    if hung:
        state = (f" The game window is NOT RESPONDING ({', '.join(hung)}): it hung while loading. Suspect the last "
                 "deploy (a new .khn/stats/Lua file); bisect by removing the change and restarting.")
    else:
        try:
            name, lines = se.log_tail(None, 40)
            if any("Cannot queue server commands in game state Uninitialized" in l for l in lines):
                state = (" No server session yet (main menu, or a load still running): -continueGame didn't load the "
                         "save, often because a dialog is waiting (e.g. the save was made with a different mod build). "
                         "Accept it or load the save by hand.")
        except RuntimeError:
            pass
    log.append(f"not loaded within {timeout}s.{state or ' Check the game window (main menu? crash?)'}")
    return "\n".join(log)
