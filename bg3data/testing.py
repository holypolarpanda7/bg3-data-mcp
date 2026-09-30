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
SCRIPT_SPELL_SOURCES = {"Osiris"}  # spells added by script (Osi.AddSpell): not class-sourced
GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


# ------------------------------------------------------------------ config
def game_cfg():
    cfg = sources.load_config()
    g = dict(cfg.get("game") or {})
    g.setdefault("steam_exe", r"C:\Program Files (x86)\Steam\steam.exe")
    g.setdefault("app_id", 1086940)
    g.setdefault("launch_args", ["--skip-launcher", "-continueGame"])
    g.setdefault("savegames", "/mnt/c/Users/holyp/AppData/Local/Larian Studios/Baldur's Gate 3/PlayerProfiles/Public/Savegames/Story")
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


def ensure_harness():
    """Install (or upgrade) the harness in the running game; cheap no-op when the current version is loaded."""
    src, version = _harness_source()
    r = se.eval_lua(f"return BG3T and BG3T.version or ''", "server", timeout=15)
    if r["ok"] and r["result"] == version:
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
    errs = r["result"].get("errors") or []
    if errs:
        raise RuntimeError("harness installed but some Osiris listeners failed: " + "; ".join(map(str, errs)))
    return version


def lua(code, timeout=20):
    """Eval with the harness guaranteed present; raise on Lua errors."""
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
        for (rname, rlvl), amt in sorted(res.items()):
            got = (st["resources"].get(rname) or {}).get(str(rlvl))
            mx = got[1] if got else 0
            if mx != amt:
                warns += 1
                lines.append(f"  WARN resource {rname}[{rlvl}] max {mx:g}, progression boosts sum to {amt:g} (other passives may change it)")
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


def find_case(layer, case_id):
    for c in load_cases(layer):
        if c.get("id") == case_id:
            return c
    raise ValueError(f"no test case {case_id!r} in {suite_dirs(layer)}")


EXPECT_KEYS = {"target", "dead", "hp_change", "hp", "status_present", "status_absent", "status_applied",
               "status_removed", "resource", "level", "change", "cast", "acted_first", "damage_type", "note"}


def validate(store, active, c):
    """Problems that would make a case unrunnable, checked against the index."""
    errs = []
    for k in ("id", "title"):
        if not c.get(k):
            errs.append(f"missing `{k}`")
    aliases = {"host"} | {s.get("as") for s in c.get("spawn", [])}
    if c.get("spell") and not store.resolve(c["spell"], active):
        errs.append(f"spell {c['spell']} not found in layers {active}")
    if c.get("target") and c["target"] not in aliases:
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
        out.append(f"{c['id']}  L{c.get('level', '?')} {c.get('class', '')}  [{c.get('mode', 'player')}] {c.get('title', '')}"
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
    mode = c.get("mode", "player")
    lua("BG3T.cleanup(); return true")
    st = host_state()
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
    if c.get("spell"):
        src = {s["id"]: s["source"] for s in st["spells"]}.get(c["spell"])
        if src is None and mode == "player":
            blockers.append(f"host doesn't know {c['spell']} - learn it through level-up / the class spell list")
        elif src in SCRIPT_SPELL_SOURCES and mode == "player":
            blockers.append(f"{c['spell']} was added by script (source {src}): it won't pay costs - learn it via level-up")
        elif src:
            notes.append(f"{c['spell']} known, source {src}")
    if st["in_combat"]:
        blockers.append("host is already in combat - finish or reload first")
    if blockers:
        return {"ok": False, "blockers": blockers, "notes": notes}

    spawns = c.get("spawn", [])
    hostile = any(s.get("faction", "hostile") == "hostile" for s in spawns)
    combat = c.get("combat", hostile)
    code = ["local r={}"]
    for i, s in enumerate(spawns):
        tpl = TEMPLATES.get(s["template"], (s["template"], None))[0]
        fac = FACTIONS.get(s.get("faction", "hostile"), s.get("faction"))
        dist = float(s.get("distance", 8))
        ang = (i - (len(spawns) - 1) / 2) * 0.6
        dx, dz = dist * math.cos(ang), dist * math.sin(ang)
        code.append(f"r[{se._lua_string(s['as'])}]=BG3T.spawn({se._lua_string(s['as'])},{se._lua_string(tpl)},{se._lua_string(fac)},{dx:.2f},{dz:.2f})")
    code.append("return r")
    spawned = lua("; ".join(code))
    missing = [s["as"] for s in spawns if not (spawned or {}).get(s["as"])]
    if missing:
        lua("BG3T.cleanup(); return true")
        raise RuntimeError(f"spawn failed for {missing}")
    # HP: raise max first (applies next tick), then set exact values
    pre = []
    for s in spawns:
        if s.get("hp") is not None:
            pre.append(f"local g=BG3T.spawns[{se._lua_string(s['as'])}]; local m=Osi.GetMaxHitpoints(g); "
                       f"if {int(s['hp'])}>m then BG3T.grant(g,'IncreaseMaxHP('..({int(s['hp'])}-m)..')') end")
    for st_ in c.get("setup", []):
        if st_.get("max_hp"):
            pre.append(f"BG3T.grant({_who(st_)}, 'IncreaseMaxHP({int(st_['max_hp'])})')")
    if pre:
        lua("; ".join(pre) + "; return true")
        time.sleep(1.0)
    post = []
    for s in spawns:
        if s.get("hp") is not None:
            post.append(f"BG3T.setHp(BG3T.spawns[{se._lua_string(s['as'])}], {int(s['hp'])})")
    for st_ in c.get("setup", []):
        if "hp" in st_:
            post.append(f"BG3T.setHp({_who(st_)}, {st_['hp'] if isinstance(st_['hp'], int) else repr('full')})")
        if st_.get("status"):
            post.append(f"BG3T.apply({_who(st_)}, {se._lua_string(st_['status'])}, {int(st_.get('turns', 10))})")
        if st_.get("boost"):
            post.append(f"BG3T.grant({_who(st_)}, {se._lua_string(st_['boost'])})")
    first = combat and c.get("initiative", "host_first") == "host_first"
    if first:
        post.append("BG3T.grant(BG3T.host(), 'Initiative(50)')")
    floor = int(c.get("safety_floor", 35))
    post.append(f"BG3T.safety.floor={floor}; BG3T.safety.enabled={'true' if c.get('safety', True) else 'false'}")
    post.append("local since=BG3T.seq; BG3T.recording=true")
    if combat:
        post.append("BG3T.enterCombat()")
    post.append("return {since=since, world=BG3T.world()}")
    since = lua("; ".join(post), timeout=30)["since"]
    time.sleep(1.0)
    before = lua("return BG3T.world()", timeout=30)
    first_turn = None
    if combat:
        for _ in range(40):
            evs = lua(f"return BG3T.drain({since})") or []
            turns = [e for e in evs if e.get("kind") == "TurnStarted"]
            if turns:
                first_turn = turns[0]
                break
            time.sleep(0.5)
    state = {"layer": layer, "case": c["id"], "mode": mode, "combat": combat, "since": since, "before": before,
             "first_turn": first_turn, "staged_at": time.time()}
    _save_state(state)
    if mode == "script" and c.get("spell"):
        tgt = "BG3T.host()" if c.get("target", "host") == "host" else f"BG3T.spawns[{se._lua_string(c['target'])}]"
        lua(f"BG3T.cast(BG3T.host(), {se._lua_string(c['spell'])}, {tgt}); return true")
    return {"ok": True, "case": c, "notes": notes, "combat": combat, "first_turn": first_turn,
            "spell_name": _name(store, active, c["spell"]) if c.get("spell") else None, "before": before}


def _who(st_):
    t = st_.get("target", "host")
    return "BG3T.host()" if t == "host" else f"BG3T.spawns[{se._lua_string(t)}]"


def verify(store, active, cleanup=True, wait=2.0):
    state = _load_state()
    if not state:
        raise ValueError("nothing staged (run bg3_test_stage first)")
    c = find_case(state["layer"], state["case"])
    time.sleep(max(0.0, min(wait, 30)))
    after = lua("return BG3T.world()", timeout=30)
    events = lua(f"return BG3T.drain({state['since']})", timeout=30) or []
    before = state["before"]
    mode = state["mode"]
    player = mode == "player"
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
        for s in e.get("status_removed", []):
            was = s in (b.get("statuses") or [])
            row(was and s not in (a.get("statuses") or []), f"{s} removed from {label}" + ("" if was else " (it wasn't present before!)"))
        if "resource" in e:
            lvl = str(e.get("level", 0))
            bv = ((b.get("resources") or {}).get(e["resource"]) or {}).get(lvl)
            av = ((a.get("resources") or {}).get(e["resource"]) or {}).get(lvl)
            if not player:
                row(None, f"{e['resource']}[{lvl}] change - not testable in script mode (scripted casts never pay costs)")
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
        if e.get("damage_type"):
            hits = [x for x in events if x.get("kind") == "Damage" and x.get("who") == guid]
            row(any(x.get("type") == e["damage_type"] for x in hits), f"{label} took {e['damage_type']} damage (seen: {sorted({x.get('type') for x in hits}) or 'none'})")
    safety = [x for x in events if x.get("kind") == "SAFETY"]
    fid = ("player cast" + (" in real combat (initiative)" if state["combat"] else " out of combat")) if player \
        else "SCRIPTED cast - effects only; slot/resource costs and turn flow NOT tested"
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
        rep = lua("return BG3T.cleanup()")
        lines.append(f"  cleanup: {rep.get('spawns', 0)} spawns, {rep.get('grants', 0)} boosts, {rep.get('statuses', 0)} statuses removed")
        try:
            os.remove(STATE_FILE)
        except OSError:
            pass
    return "\n".join(lines)


def cleanup():
    rep = lua("return BG3T.cleanup()")
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass
    return rep


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
            mode = c.get("mode", "player")
            md += [f"### {lvl}.{i} {c.get('title', c['id'])}", "", f"Case `{c['id']}` - {'you cast it (real play)' if mode == 'player' else 'I cast it by script (effects only)'}.", ""]
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
            elif mode == "script":
                steps.append("Watch: I cast it for you.")
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
    try:
        return subprocess.run(["tasklist.exe"], capture_output=True, text=True, timeout=20).stdout.lower()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def kill_game():
    _, g = game_cfg()
    killed = []
    for p in g["processes"]:
        if p.lower() in _tasklist():
            subprocess.run(["taskkill.exe", "/IM", p, "/F"], capture_output=True, timeout=30)
            killed.append(p)
    for _ in range(30):
        if not any(p.lower() in _tasklist() for p in g["processes"]):
            break
        time.sleep(1)
    return killed


def newest_save():
    _, g = game_cfg()
    saves = [d for d in glob.glob(os.path.join(g["savegames"], "*")) if os.path.isdir(d)]
    if not saves:
        return None
    s = max(saves, key=os.path.getmtime)
    return os.path.basename(s), sources.iso(os.path.getmtime(s))


def restart(deploy_layer=None, launch=True, timeout=300):
    log = []
    killed = kill_game()
    log.append("killed: " + (", ".join(killed) or "game wasn't running"))
    if deploy_layer:
        m = mod_entry(deploy_layer)
        cmd = m.get("deploy")
        if not cmd:
            raise ValueError(f"layer {deploy_layer!r} has no `deploy` command in layers.json")
        r = subprocess.run(["bash", "-lc", cmd], cwd=m["path"], capture_output=True, text=True, timeout=600)
        tail = (r.stdout + r.stderr).strip().splitlines()[-8:]
        log.append(f"deploy ({cmd}) exit {r.returncode}:\n    " + "\n    ".join(tail))
        if r.returncode != 0:
            return "\n".join(log + ["deploy failed - not launching"])
    if not launch:
        return "\n".join(log)
    ns = newest_save()
    log.append(f"newest save (what -continueGame loads): {ns[0]} ({ns[1]})" if ns else "no saves found")
    _, g = game_cfg()
    args = " ".join(g["launch_args"])
    subprocess.run(["cmd.exe", "/c", "start", "", g["steam_exe"], "-applaunch", str(g["app_id"])] + g["launch_args"],
                   cwd="/mnt/c", capture_output=True, timeout=30)
    log.append(f"launched via Steam -applaunch {g['app_id']} {args}")
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(5)
        try:
            r = se.eval_lua("return Osi.GetHostCharacter() and Osi.GetRegion(Osi.GetHostCharacter()) or ''", "server", timeout=8)
            if r["ok"] and r["result"]:
                log.append(f"session loaded after {time.time() - t0:.0f}s: host in {r['result']}")
                return "\n".join(log)
        except (RuntimeError, TimeoutError):
            pass
    log.append(f"not loaded within {timeout}s - check the game window (main menu? crash?)")
    return "\n".join(log)
