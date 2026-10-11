"""Vortex load order: sort the mods Vortex has enabled for BG3 into the order that gives the best compatibility.

Vortex's BG3 extension keeps its load order in %APPDATA%/Vortex/baldursgate3/<profile>_loadOrder.json (a list of
{id, enabled, locked, index, data{uuid, folder, name, ...}}) and writes modsettings.lsx from it on every deploy. A later
entry wins a conflict, so the sort is:
  1. hard rule - a mod loads after every mod it depends on (meta.lsx Dependencies, read from each pak); cycles are reported;
  2. tiers (lowest first): libraries/frameworks (no game data of their own, or a known library such as Mod Configuration Menu)
     -> standalone content -> content built on other mods -> patches/compat/fix mods;
  3. pins from layers.json `vortex.order`: {"first": [names], "last": [names], "after": {"Mod": ["Other", ...]}};
  4. otherwise the current order is kept (the sort is stable), so a hand-made choice between unrelated mods survives.
Reported (never auto-resolved): enabled mods whose dependency is missing/disabled, and files two mods both ship (the later wins).
`apply` rewrites the load order file; Vortex must be closed (it holds the state in memory and would overwrite the file on exit).
"""
import glob
import json
import os
import re
import shutil
import tempfile
import time

from . import deploy, platform, sources, vortex

# modules the game itself provides: a dependency on one of these is not a mod
BASE_MODULES = {"gustav", "gustavdev", "shared", "shareddev", "honour", "honourx", "main", "mainui", "game", "fw3", "photomode", "modbrowser", "crossplayui",
                "crimesandpunishment", "diceset_01", "diceset_02", "diceset_03", "diceset_04", "diceset_05", "diceset_06"}
BASE_UUIDS = {"991c9c7a-fb80-40cb-8f0d-b92d4e80e9b1", "28ac9ce2-2aba-8cda-b3b5-6e922f71b6b8", "ed539163-bb70-431b-96a7-f5b2eda5dcc1",
              "3d0c5ff5-3a48-6eaf-4a5c-4e9a1bd1bbfb", "cb555efe-2d9e-131f-8195-a89329d218ea", "68a99fef-d125-4ed0-893f-bb6751e52c5e",
              "e842bf9a-7ac7-8fc3-a6bc-8fd6b9a9be91"}
LIB_NAMES = re.compile(r"mod ?configuration ?menu|improved ?ui|library|framework|volition|script ?extender|\bapi\b|\bcore\b", re.I)
PATCH_NAMES = re.compile(r"patch|compat|hotfix|\bfix(es)?\b|override|tweak", re.I)
# files every mod ships and that never conflict in a meaningful way
HARMLESS = re.compile(r"(^|/)(meta\.lsx|.*\.loca|.*\.png|.*\.dds|.*\.json|.*\.md|.*\.txt)$", re.I)
GAME_DATA = re.compile(r"^(Public/[^/]+/(Stats|Content|Assets|Localization|RootTemplates|ClassDescriptions|Progressions|Lists|"
                       r"Races|Feats|Tags|ActionResources|ActionResourceGroups|CharacterCreation)|Mods/[^/]+/Localization)", re.I)
_ATTR = re.compile(r'<attribute id="(\w+)" type="\w+" value="([^"]*)"')


def order_file(profile=None, cfg=None):
    """(path, profile id) of the load order file; the most recently written one when several profiles have one."""
    base = os.path.join(vortex.app_dir(cfg or sources.load_config()), "baldursgate3")
    if profile:
        p = os.path.join(base, f"{profile}_loadOrder.json")
        return (p, profile) if os.path.exists(p) else (None, profile)
    files = sorted(glob.glob(os.path.join(base, "*_loadOrder.json")), key=os.path.getmtime, reverse=True)
    return (files[0], os.path.basename(files[0])[:-len("_loadOrder.json")]) if files else (None, None)


def _pak_path(cfg, entry, mods_dir):
    p = os.path.join(mods_dir, entry["id"])
    return p if os.path.exists(p) else None


def _pak_info(cfg, pak):
    """{'files': [pak paths], 'deps': [{UUID, Name, Folder}], 'meta': {...ModuleInfo}} read with Divine."""
    listing = [l.split("\t")[0].replace("\\", "/") for l in sources.divine(cfg, "-a", "list-package", "-s", platform.to_win(pak)).splitlines()
               if "\t" in l]
    metas = [f for f in listing if re.fullmatch(r"Mods/[^/]+/meta\.lsx", f, re.I)]
    deps, meta = [], {}
    if metas:
        work = tempfile.mkdtemp(prefix="vortex_order_")
        try:
            dest = os.path.join(work, "meta.lsx")
            sources.divine(cfg, "-a", "extract-single-file", "-s", platform.to_win(pak), "-f", metas[0], "-d", platform.to_win(dest))
            deps_meta = deploy.parse_meta(dest)
            meta, deps = deps_meta
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return {"files": listing, "deps": deps, "meta": meta}


def _tier(name, uuid, info, has_mod_dep):
    if LIB_NAMES.search(name):
        return 0
    if not any(GAME_DATA.match(f) for f in info["files"]):
        return 0  # nothing the game's data layer could conflict on: a script/utility library
    if PATCH_NAMES.search(name):
        return 3
    return 2 if has_mod_dep else 1


def bridge_up():
    r = vortex.action("status")
    return isinstance(r, dict) and not r.get("error")


def analyse(profile=None, cfg=None, live=False):
    """Read the load order (Vortex's live state through the bridge when `live`, else the file) + every enabled mod's pak."""
    cfg = cfg or sources.load_config()
    path, prof = order_file(profile, cfg)
    if live:
        r = vortex.action("loadorder")
        if r.get("error"):
            raise RuntimeError(r["error"])
        entries, prof = r.get("loadOrder") or [], r.get("profile") or prof
    elif not path:
        raise RuntimeError("no Vortex baldursgate3/<profile>_loadOrder.json found - open the Load Order page in Vortex once")
    else:
        entries = json.load(open(path, encoding="utf-8-sig"))
    _, mods_dir, _ = deploy._paths(cfg)
    pins = (cfg.get("vortex") or {}).get("order") or {}
    by_uuid = {e["data"]["uuid"].lower(): e for e in entries if (e.get("data") or {}).get("uuid")}
    problems, infos = [], {}
    for e in entries:
        if not e.get("enabled"):
            continue
        pak = _pak_path(cfg, e, mods_dir)
        if not pak:
            problems.append(f"{e['name']}: enabled but {e['id']} is not in the Mods folder (deploy first) - ordered by its position only")
            infos[e["id"]] = {"files": [], "deps": [], "meta": {}}
            continue
        infos[e["id"]] = _pak_info(cfg, pak)
    return {"path": path, "profile": prof, "entries": entries, "infos": infos, "by_uuid": by_uuid, "pins": pins, "problems": problems}


def _pin_match(names, e):
    keys = {e["name"].lower(), e["id"].lower(), (e["data"].get("folder") or "").lower()}
    return any(n.lower() in keys or n.lower() in e["name"].lower() for n in names)


def sort(a):
    """-> (ordered enabled entries, notes). Dependencies first, then tier, pins, current order."""
    entries, infos, by_uuid, pins = a["entries"], a["infos"], a["by_uuid"], a["pins"]
    on = [e for e in entries if e.get("enabled")]
    notes = list(a["problems"])
    uid = {e["id"]: e["data"]["uuid"].lower() for e in on}
    on_uuids = set(uid.values())
    deps = {}  # id -> set of ids it must follow
    for e in on:
        need = set()
        for d in infos[e["id"]]["deps"]:
            du = d["UUID"].lower()
            if du in on_uuids:
                need.add(next(i for i, u in uid.items() if u == du))
            elif du in BASE_UUIDS or (d.get("Folder") or d.get("Name") or "").lower() in BASE_MODULES:
                continue
            elif du in by_uuid:
                notes.append(f"!! {e['name']} needs {d.get('Name') or du}, which is in Vortex but DISABLED - enable it")
            else:
                notes.append(f"!! {e['name']} needs {d.get('Name') or du}, which is not installed in Vortex")
        deps[e["id"]] = need
    # "after" pins become extra dependencies (by name)
    for who, afters in (pins.get("after") or {}).items():
        for e in on:
            if _pin_match([who], e):
                deps[e["id"]] |= {o["id"] for o in on if o is not e and _pin_match(afters if isinstance(afters, list) else [afters], o)}

    def key(e):
        t = _tier(e["name"], uid[e["id"]], infos[e["id"]], bool(deps[e["id"]]))
        if _pin_match(pins.get("first") or [], e):
            t = -1
        elif _pin_match(pins.get("last") or [], e):
            t = 9
        return (t, e.get("index", 0))

    # stable topological sort: repeatedly take the lowest-key mod whose dependencies are all placed
    placed, out, pending = set(), [], sorted(on, key=key)
    while pending:
        ready = [e for e in pending if deps[e["id"]] <= placed]
        if not ready:  # cycle: place the rest in key order and say so
            notes.append("!! dependency cycle among: " + ", ".join(e["name"] for e in pending))
            ready = pending[:1]
        # a tier is only a preference: a ready mod from a higher tier never jumps ahead of a lower-tier one that is merely blocked
        e = ready[0]
        out.append(e)
        placed.add(e["id"])
        pending.remove(e)
    return out, notes


def conflicts(a, ordered):
    """Files shipped by two or more enabled mods (the later one in `ordered` wins)."""
    seen, out = {}, {}
    for e in ordered:
        for f in a["infos"][e["id"]]["files"]:
            if HARMLESS.search(f) or f.endswith("/"):
                continue
            seen.setdefault(f.lower(), []).append(e["name"])
    for f, who in seen.items():
        if len(who) > 1:
            out.setdefault(tuple(who), []).append(f)
    return out


def _verify(ordered, cfg):
    """modsettings.lsx (what the game reads) vs the wanted order + the paks in the Mods folder. -> (ok, lines)"""
    _, mods_dir, ms = deploy._paths(cfg)
    lines, ok = [], True
    try:
        have = [d["Name"] for _, d in deploy._nodes(open(ms, encoding="utf-8").read())]
        got = [u.lower() for u, _ in deploy._nodes(open(ms, encoding="utf-8").read())]
    except OSError:
        return False, [f"modsettings.lsx not found at {ms}"]
    want = [e["data"]["uuid"].lower() for e in ordered]
    # locked entries (ModFixer) are kept out of modsettings.lsx by Vortex on purpose
    missing = [e["name"] for e in ordered if e["data"]["uuid"].lower() not in got and not e.get("locked")]
    if missing:
        ok = False
        lines.append("NOT in modsettings.lsx (the game will not load them): " + ", ".join(missing))
    in_game = [u for u in got if u in want]
    if in_game != [u for u in want if u in got]:
        ok = False
        lines.append("modsettings.lsx order differs from the sorted order")
    for e in ordered:
        if not os.path.exists(os.path.join(mods_dir, e["id"])):
            ok = False
            lines.append(f"{e['id']} is not in the Mods folder")
    stale = [d["Name"] for u, d in deploy._nodes(open(ms, encoding="utf-8").read())
             if u.lower() not in want and u.lower() not in BASE_UUIDS and (d.get("Folder") or "").lower() not in BASE_MODULES]
    if stale:
        lines.append("also enabled in modsettings.lsx (not in Vortex's list): " + ", ".join(stale))
    return ok, lines


def report(profile=None, apply=False, remove=(), deploy_after=True):
    cfg = sources.load_config()
    out, removed_paks = [], set()
    if apply:
        if deploy.game_running():
            return "Baldur's Gate 3 is running - close it first (a deploy under a running game is not safe)"
        if not bridge_up():
            if remove or deploy_after:
                return ("Vortex's bridge is not reachable: start Vortex (and install the bridge once with bg3_vortex_bridge_install) "
                        "- removing mods and deploying go through it")
        else:
            _, mods_dir, _ = deploy._paths(cfg)
            for m in remove:
                managed = [x.get("id") or "" for x in (vortex.action("status").get("mods") or [])]
                r = vortex.action("remove", m) if any(m.lower() in i.lower() for i in managed) else {"error": "no mod matching"}
                if isinstance(r, dict) and "no mod matching" in str(r.get("error")):  # not a Vortex mod: a loose pak in the Mods folder
                    hits = [f for f in os.listdir(mods_dir) if f.lower().endswith(".pak") and m.lower() in f.lower()]
                    if len(hits) == 1:
                        dest = os.path.join(mods_dir, "_removed")
                        os.makedirs(dest, exist_ok=True)
                        shutil.move(os.path.join(mods_dir, hits[0]), os.path.join(dest, hits[0]))
                        removed_paks.add(hits[0])
                        out.append(f"remove {m}: loose pak {hits[0]} moved to Mods/_removed (not a Vortex mod)")
                        continue
                    out.append(f"remove {m}: no Vortex mod and {len(hits)} loose paks match - nothing removed")
                    continue
                out.append(f"remove {m}: " + vortex.format_result(r).splitlines()[0])
            if remove or deploy_after:
                out.append("deploy: " + vortex.format_result(vortex.action("deploy")).splitlines()[0])
    live = apply and bridge_up()
    a = analyse(profile, cfg, live=live)
    a["entries"] = [e for e in a["entries"] if e["id"] not in removed_paks]
    ordered, notes = sort(a)
    entries = a["entries"]
    now_on = [e for e in sorted(entries, key=lambda e: e.get("index", 0)) if e.get("enabled")]
    out.append(f"Vortex BG3 profile {a['profile']}: {len(ordered)} enabled / {len(entries)} mods")
    same = [e["id"] for e in now_on] == [e["id"] for e in ordered]
    out.append("The load order is already the sorted one:" if same else
               "Current -> sorted (earlier loads first, later wins conflicts):")
    rows = []
    for i, e in enumerate(ordered):
        was = next(j for j, x in enumerate(now_on) if x["id"] == e["id"])
        t = _tier(e["name"], "", a["infos"][e["id"]], bool([1 for d in a["infos"][e["id"]]["deps"] if d["UUID"].lower() in a["by_uuid"]]))
        rows.append((t, f"  {i + 1:>3}. {e['name']}  [{('library', 'standalone', 'built on mods', 'patch')[min(t, 3)]}]"
                        + (f"   (was {was + 1})" if was != i else "")))
    if len(rows) <= 40:
        out += [r for _, r in rows]
    else:  # long lists: libraries, the non-standalone mods, and a count for the standalone block
        shown, skipped = [], 0
        for t, r in rows:
            if t == 1:
                skipped += 1
                if skipped == 1:
                    shown.append(None)
            else:
                shown.append(r)
        out += [r if r else f"       ... {skipped} standalone content mods (kept in their current order) ..." for r in shown]
    cfl = conflicts(a, ordered)
    for who, files in list(cfl.items())[:10]:
        names = ' < '.join(who) if len(who) <= 4 else f"{who[0]} ... {who[-2]} < {who[-1]} ({len(who)} mods)"
        out.append(f"  overlap: {names} both ship {len(files)} file(s), e.g. {files[0]}  ({who[-1]} wins)")
    out += ["  " + n for n in notes]
    if not apply:
        return "\n".join(out + ["(dry run - apply=True removes the `remove` mods, deploys, sorts, deploys again and checks the game's mod list)"])
    if any("cycle" in n for n in notes):
        return "\n".join(out + ["NOT sorted: dependency cycle (nothing else was changed)"])
    disabled = [e for e in sorted(entries, key=lambda e: e.get("index", 0)) if not e.get("enabled")]
    final = ordered + disabled
    for i, e in enumerate(final):
        e["index"] = i
    if live:
        r = vortex.bridge_call("POST", "/loadorder", {"loadOrder": final})
        if r.get("error"):
            return "\n".join(out + [f"bridge could not set the load order: {r['error']}"])
        out.append("load order set in Vortex")
    else:  # bridge unavailable: only the on-disk file can be changed, and only while Vortex is closed
        if "vortex.exe" in platform.tasklist():
            return "\n".join(out + ["NOT applied: Vortex is running without its bridge - install it (bg3_vortex_bridge_install), restart Vortex"])
        shutil.copy2(a["path"], a["path"] + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
        json.dump(final, open(a["path"], "w", encoding="utf-8"), separators=(",", ":"))
        out.append("load order file rewritten (backup beside it); start Vortex and Deploy")
        return "\n".join(out)
    if deploy_after:
        out.append("deploy: " + vortex.format_result(vortex.action("deploy")).splitlines()[0])
        time.sleep(2)
        ok, lines = _verify(ordered, cfg)
        out += ["  " + l for l in lines]
        warn = [n for n in notes if n.startswith("!!")]
        out.append(("READY" + (f" ({len(warn)} missing/disabled-dependency warning(s) above)" if warn else "")
                    + ": modsettings.lsx lists the mods in the sorted order and every pak is in the Mods folder") if ok
                   else "NOT READY - see the lines above")
    return "\n".join(out)
