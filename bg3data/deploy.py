"""Pack, deploy and enable a mod layer, for any mod and any setup (no per-mod scripts).

  pack      Mods/<folder> + Public/<folder> -> <folder>.pak with LSLib's Divine; checks meta.lsx is inside
  deploy    copies the pak into the user Mods folder (refuses while the game runs; backs up the old pak)
  enable    adds the mod to the active profile's modsettings.lsx after its dependencies (backup kept)

Mod managers: Vortex/BG3 Mod Manager may rewrite modsettings.lsx when they deploy or export a load order,
which disables a mod they don't manage; re-running enable (or bg3_deploy) puts it back. See bg3_environment.
"""
import glob
import json
import os
import re
import shutil
import time

from . import platform, sources


def mod_info(layer):
    """The mod's own module info and dependencies, read from Mods/<folder>/meta.lsx."""
    cfg = sources.load_config()
    m = next((x for x in cfg["mods"] if x["name"] == layer), None)
    if not m:
        raise ValueError(f"unknown mod layer {layer!r}")
    if m["path"].lower().endswith(".pak"):
        raise ValueError("deploy needs the mod's source folder, not a .pak layer")
    metas = glob.glob(os.path.join(m["path"], "Mods", "*", "meta.lsx"))
    if len(metas) != 1:
        raise ValueError(f"expected one Mods/<folder>/meta.lsx in {m['path']}, found {len(metas)}")
    info, deps = parse_meta(metas[0])
    return cfg, m, info, deps


def parse_meta(meta_path):
    """(ModuleInfo dict, [dependency ModuleShortDesc dicts]) from a Mods/<folder>/meta.lsx."""
    text = open(meta_path, encoding="utf-8").read()
    info_block = re.search(r'<node id="ModuleInfo">(.*?)(?:<children>|</node>)', text, re.S)
    attrs = dict(re.findall(r'<attribute id="(\w+)" type="\w+" value="([^"]*)"', info_block.group(1) if info_block else ""))
    deps_block = re.search(r'<node id="Dependencies">\s*<children>(.*?)</children>\s*</node>', text, re.S)
    deps = []
    for blk in re.findall(r'<node id="ModuleShortDesc">(.*?)</node>', deps_block.group(1) if deps_block else "", re.S):
        d = dict(re.findall(r'<attribute id="(\w+)" type="\w+" value="([^"]*)"', blk))
        if d.get("UUID"):
            deps.append(d)
    folder = attrs.get("Folder") or os.path.basename(os.path.dirname(meta_path))
    return {"Folder": folder, "Name": attrs.get("Name", folder), "UUID": attrs.get("UUID"),
            "Version64": attrs.get("Version64", "36028797018963968"), "PublishHandle": attrs.get("PublishHandle", "0")}, deps


def _paths(cfg):
    ld = platform.larian_dir(cfg)
    if not ld:
        raise RuntimeError("Larian user folder not found (%LOCALAPPDATA%\\Larian Studios\\Baldur's Gate 3); set game.larian_dir in layers.json")
    profile = (cfg.get("game") or {}).get("profile", "Public")
    return ld, os.path.join(ld, "Mods"), os.path.join(ld, "PlayerProfiles", profile, "modsettings.lsx")


def deployed_lines(store):
    """For each folder mod layer: the pak the game actually loads (user Mods folder; a mod manager's symlink is
    followed) and whether it is older than the indexed sources. The index can be ahead of the game: e.g. a git
    clone of a mod while the game runs its last release - in-game results then differ from what the index says."""
    cfg = sources.load_config()
    try:
        _, mods_dir, _ = _paths(cfg)
    except RuntimeError:
        return []
    newest = {r[0]: r[4] for r in store.layer_rows()}
    out = []
    for m in cfg["mods"]:
        try:
            _, _, info, _ = mod_info(m["name"])
        except ValueError:
            continue
        pak = os.path.join(mods_dir, info["Folder"] + ".pak")
        if not os.path.exists(pak):
            out.append(f"  {m['name']}: no {info['Folder']}.pak in the user Mods folder (not deployed there)")
            continue
        real, mt = os.path.realpath(pak), os.path.getmtime(pak)
        line = f"  {m['name']}: game loads {real if real != pak else os.path.basename(pak)} ({sources.iso(mt)})"
        if newest.get(m["name"]) and mt + 3600 < newest[m["name"]]:
            line += (f"\n     !! OLDER than the indexed sources ({sources.iso(newest[m['name']])}): the game runs an older "
                     f"{m['name']} than the index describes - entries added since are missing in game")
        out.append(line)
    return (["Deployed (what the running game loads):"] + out) if out else []


def game_running():
    tl = platform.tasklist()
    return any(p in tl for p in ("bg3.exe", "bg3_dx11.exe"))


def pack(layer):
    cfg, m, info, _ = mod_info(layer)
    folder = info["Folder"]
    out_dir = os.path.join(platform.windows_temp(), "bg3data_build", folder)
    stage = os.path.join(out_dir, "stage")
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    for part in ("Mods", "Public"):
        src = os.path.join(m["path"], part, folder)
        if not os.path.isdir(src):
            if part == "Mods":
                raise ValueError(f"missing {src}")
            continue
        shutil.copytree(src, os.path.join(stage, part, folder), ignore=shutil.ignore_patterns("*.lsx.bak", "Thumbs.db"))
    pak = os.path.join(out_dir, folder + ".pak")
    if os.path.exists(pak):
        os.remove(pak)
    sources.divine(cfg, "-a", "create-package", "-s", platform.to_win(stage), "-d", platform.to_win(pak))
    listing = [l.split("\t")[0].replace("\\", "/") for l in sources.divine(cfg, "-a", "list-package", "-s", platform.to_win(pak)).splitlines() if "\t" in l]
    if f"Mods/{folder}/meta.lsx" not in listing:
        raise RuntimeError(f"packed pak has no Mods/{folder}/meta.lsx - refusing to deploy it")
    return pak, len(listing)


def enable(layer):
    """Add the mod to modsettings.lsx (after its dependencies). Returns a list of report lines."""
    cfg, m, info, deps = mod_info(layer)
    _, _, ms = _paths(cfg)
    if not os.path.exists(ms):
        return [f"modsettings.lsx not found at {ms} (start the game once to create it)"]
    text = open(ms, encoding="utf-8").read()
    lines = []
    present = set(re.findall(r'id="UUID" type="\w+" value="([^"]+)"', text))
    base_modules = {"GustavX", "GustavDev", "Gustav", "Shared", "SharedDev", "Honour", "HonourX", "MainUI", "ModBrowser",
                    "PhotoMode", "CrossplayUI", "DiceSet_01", "DiceSet_02", "DiceSet_03", "DiceSet_06"}
    missing = [d.get("Name") or d.get("Folder") for d in deps if d["UUID"] not in present and d.get("Folder") not in base_modules]
    if missing:
        lines.append(f"WARNING: dependencies not enabled in modsettings.lsx: {', '.join(missing)}")
    if info["UUID"] in present:
        def rename(mm):  # a renamed mod keeps its UUID: refresh the entry's Folder / Name
            b = mm.group(0)
            if f'value="{info["UUID"]}"' not in b:
                return b
            b = re.sub(r'(id="Folder" type="LSString" value=")[^"]*"', lambda x: x.group(1) + info["Folder"] + '"', b)
            return re.sub(r'(id="Name" type="LSString" value=")[^"]*"', lambda x: x.group(1) + info["Name"] + '"', b)
        new = re.sub(r'<node id="ModuleShortDesc">.*?</node>', rename, text, flags=re.S)
        if new != text:
            shutil.copy2(ms, ms + time.strftime(".%Y%m%d_%H%M%S.bak"))
            open(ms, "w", encoding="utf-8", newline="").write(new)
            lines.append(f"updated the modsettings.lsx entry to Folder {info['Folder']} / Name {info['Name']} (renamed mod)")
        return lines + [f"{info['Name']} is enabled in {os.path.basename(os.path.dirname(ms))}/modsettings.lsx"]
    shutil.copy2(ms, ms + time.strftime(".%Y%m%d_%H%M%S.bak"))
    node = ("                        <node id=\"ModuleShortDesc\">\n"
            f"                            <attribute id=\"Folder\" type=\"LSString\" value=\"{info['Folder']}\"/>\n"
            "                            <attribute id=\"MD5\" type=\"LSString\" value=\"\"/>\n"
            f"                            <attribute id=\"Name\" type=\"LSString\" value=\"{info['Name']}\"/>\n"
            f"                            <attribute id=\"PublishHandle\" type=\"uint64\" value=\"{info['PublishHandle']}\"/>\n"
            f"                            <attribute id=\"UUID\" type=\"guid\" value=\"{info['UUID']}\"/>\n"
            f"                            <attribute id=\"Version64\" type=\"int64\" value=\"{info['Version64']}\"/>\n"
            "                        </node>\n")
    mods = re.search(r'<node id="Mods">\s*<children>', text)
    if not mods:
        return lines + ["modsettings.lsx has no Mods node - enable the mod in your mod manager"]
    end = text.index("</children>", mods.end())  # end of the Mods list = after every dependency
    line_start = text.rfind("\n", 0, end) + 1
    text = text[:line_start] + node + text[line_start:]
    open(ms, "w", encoding="utf-8", newline="").write(text)
    return lines + [f"enabled {info['Name']} in modsettings.lsx (last in load order, after its dependencies; backup kept)"]


# ---------------------------------------------------------------- test isolation (2026-10-07)
# A test must load only the mods it is about: the layer, its declared dependencies and the mods the layer lists as `test_mods`
# in layers.json ([{"name": "dnd55e", "uuid": "..."}]). Anything else the profile has enabled (another mod project's pak, a
# retired mod) can mask or cause a result. isolate() trims the active profile's modsettings.lsx to that set, backing the original up;
# restore_isolation() puts it back. Saves made with a bigger mod list then load with a "missing mods" box, which
# gameui.load_save tolerates for exactly the mods removed here (the list is kept in isolation.json).
ISOLATION = os.path.join(sources.CACHE, "isolation.json")
BASE_MODULE_NAMES = {"GustavX", "GustavDev", "Gustav", "Shared", "SharedDev", "Honour", "HonourX", "MainUI", "ModBrowser",
                     "PhotoMode", "CrossplayUI", "DiceSet_01", "DiceSet_02", "DiceSet_03", "DiceSet_06"}


def isolation_state():
    try:
        return json.load(open(ISOLATION, encoding="utf-8"))
    except (OSError, ValueError):
        return None


REGISTRY = os.path.join(sources.CACHE, "mods_registry.json")
_NODE = re.compile(r'[ \t]*<node id="ModuleShortDesc">.*?</node>[ \t]*\r?\n?', re.S)
_ATTR = re.compile(r'<attribute id="(\w+)" type="\w+" value="([^"]*)"')


def _nodes(text):
    """[(UUID, entry dict)] of the ModuleShortDesc nodes of a modsettings.lsx, in file order."""
    out = []
    for blk in _NODE.findall(text):
        d = dict(_ATTR.findall(blk))
        if d.get("UUID"):
            out.append((d["UUID"], d))
    return out


def _registry():
    try:
        return json.load(open(REGISTRY, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember(text):
    """Keep every mod entry the game / a mod manager wrote (Folder, Name, Version64, PublishHandle; MD5 is recomputed from the pak
    when a list is built), so a later list can be rebuilt for mods that are no longer in the current modsettings.lsx."""
    reg = _registry()
    for u, d in _nodes(text):
        if d.get("Folder"):
            reg[u] = {k: d.get(k, "") for k in ("Folder", "Name", "PublishHandle", "Version64", "MD5")}
    os.makedirs(os.path.dirname(REGISTRY), exist_ok=True)
    json.dump(reg, open(REGISTRY, "w", encoding="utf-8"), indent=1)
    return reg


def _entry_xml(u, d):
    pad = " " * 28
    return ("                        <node id=\"ModuleShortDesc\">\n"
            f"{pad}<attribute id=\"Folder\" type=\"LSString\" value=\"{d['Folder']}\"/>\n"
            f"{pad}<attribute id=\"MD5\" type=\"LSString\" value=\"{d.get('MD5', '')}\"/>\n"
            f"{pad}<attribute id=\"Name\" type=\"LSString\" value=\"{d['Name']}\"/>\n"
            f"{pad}<attribute id=\"PublishHandle\" type=\"uint64\" value=\"{d.get('PublishHandle') or 0}\"/>\n"
            f"{pad}<attribute id=\"UUID\" type=\"guid\" value=\"{u}\"/>\n"
            f"{pad}<attribute id=\"Version64\" type=\"int64\" value=\"{d['Version64']}\"/>\n"
            "                        </node>\n")


def desired_mods(layer, standalone=False, extra=()):
    """Ordered UUIDs a test of `layer` needs beyond the base game: its dependencies (meta.lsx order, base modules excluded), the
    layer's layers.json test_mods (none when standalone, loaded before the layer), the layer and `extra` (uuids, e.g. Mod Configuration Menu for an MCM test)."""
    cfg, m, info, deps = mod_info(layer)
    order = [d["UUID"] for d in deps if d.get("Folder") not in BASE_MODULE_NAMES and d.get("Name") not in BASE_MODULE_NAMES]
    if not standalone:       # before the layer: a mod that replaces another's content (Bigby over dnd55e) loads after it
        order += [(t["uuid"] if isinstance(t, dict) else t) for t in (m.get("test_mods") or [])]
    order.append(info["UUID"])
    order += list(extra)
    seen, out = set(), []
    for u in order:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def isolate(layer, standalone=False, extra=()):
    """Rebuild modsettings.lsx for a test: the base game's modules as they are + exactly the mods the test needs (desired_mods:
    dependencies, the layer, test_mods, extra), each entry written the way the game writes it (real MD5 of the pak on disk,
    Version64 / PublishHandle from the registry or the layer's meta.lsx). Nothing else the profile has enabled is loaded - a retired
    mod or another project's pak can mask or cause a result, and every extra mod slows the launch. The original is backed up
    once (restore_isolation puts it back); mods that were removed are recorded for the saves tolerance in gameui.
    Returns report lines."""
    from . import saves
    cfg, m, info, deps = mod_info(layer)
    _, mods_dir, ms = _paths(cfg)
    if not os.path.exists(ms):
        return [f"modsettings.lsx not found at {ms}; nothing isolated"]
    state = isolation_state()
    backup = state["backup"] if state and os.path.exists(state.get("backup", "")) else ms + ".pre-isolate"
    if not (state and os.path.exists(backup)):
        shutil.copy2(ms, backup)
    text = open(ms, encoding="utf-8").read()
    reg = remember(open(backup, encoding="utf-8").read())
    reg = remember(text)
    # the layer's own entry always comes from its meta.lsx (the Toolkit changes Version64 per publish)
    reg[info["UUID"]] = {"Folder": info["Folder"], "Name": info["Name"], "PublishHandle": info.get("PublishHandle", "0"),
                         "Version64": info["Version64"], "MD5": ""}
    want = desired_mods(layer, standalone, extra)
    current = _nodes(text)
    base = [(u, d) for u, d in current if d.get("Name") in BASE_MODULE_NAMES or d.get("Folder") in BASE_MODULE_NAMES]
    notes, entries = [], []
    for u in want:
        d = dict(reg.get(u) or {})
        if not d.get("Folder"):
            notes.append(f"{u} is needed but unknown (no entry ever seen) - skipped")
            continue
        real = saves._pak_md5(mods_dir, d["Folder"]) or saves._pak_md5(mods_dir, d["Name"])   # e.g. Vortex names MCM's pak by its title
        if real:
            d["MD5"] = real
        elif not any(os.path.exists(os.path.join(mods_dir, x + ".pak")) for x in (d["Folder"], d["Name"])):
            notes.append(f"{d['Name']}: no {d['Folder']}.pak in the Mods folder - deploy it first")
        entries.append((u, d))
    xml = "".join(_entry_xml(u, d) for u, d in base + entries)
    mods = re.search(r'(<node id="Mods">\s*<children>\n)(.*?)(\s*</children>\s*</node>)', text, re.S)
    if not mods:
        return ["modsettings.lsx has no Mods node; nothing isolated"]
    new = text[:mods.start(2)] + xml.rstrip("\n") + text[mods.end(2):]
    open(ms, "w", encoding="utf-8", newline="").write(new)
    keep = {u for u, _ in base + entries}
    removed = [{"name": d.get("Name", ""), "uuid": u} for u, d in current if u not in keep]
    prev = (state or {}).get("removed", [])
    allremoved = prev + [r for r in removed if r["uuid"] not in {x["uuid"] for x in prev}]
    json.dump({"backup": backup, "layer": layer, "removed": allremoved, "standalone": standalone,
               "loaded": [d["Name"] for _, d in entries]}, open(ISOLATION, "w", encoding="utf-8"))
    return [f"modsettings.lsx rebuilt for {layer}{' (standalone)' if standalone else ''}: {', '.join(d['Name'] for _, d in entries)} "
            f"(+ {len(base)} base modules); removed {', '.join(r['name'] or r['uuid'] for r in removed) or 'nothing'}; "
            "original backed up, restored when the run ends"] + notes


def isolate_saves(layer, only=None):
    """The game refuses to load a save whose mods are missing (Mod Verification box, Start Game disabled), so the saves an
    isolated run loads - the newest (what Continue loads) and the layer's build start/checkpoint saves - get the removed mods
    dropped from their mod list (save_backups keeps the originals; a later normal load only sees 'new mods', which is tolerated).
    only = build ids the run will actually take (their start/checkpoint saves); None = every build of the layer. A layer with
    hundreds of checkpoint saves (apotheosis) took 20+ minutes to rewrite them all, so the gate passes the builds it runs."""
    from . import saves, testing
    state = isolation_state() or {}
    removed = [r["uuid"] for r in state.get("removed", [])]
    if not removed:
        return []
    cfg = sources.load_config()
    builds = [b for b in testing.load_builds(layer) if only is None or b["id"] in only]
    names = {b["id"] for b in builds} | {b["from"] for b in builds if b.get("from")}
    _, m, _, _ = mod_info(layer)
    for f in ([] if only is not None else glob.glob(os.path.join(m["path"], "tests", "bg3", "*.toml"))):   # `save_as` names of the layer's start saves
        names |= set(re.findall(r'^save_as\s*=\s*"([^"]+)"', open(f, encoding="utf-8").read(), flags=re.M))
    targets = {d for _, d, _ in saves.list_saves(cfg, 1)}
    for _, d, _ in saves.list_saves(cfg, 10_000):
        if any(n.lower() in d.lower() for n in names):
            targets.add(d)
    out = []
    for d in sorted(targets):
        try:
            r = saves.fix_mods(cfg, d, remove=removed, sync=False, apply=True)
            out.append(f"save {d}: " + ("dropped the isolated mods" if "rewrote" in r else r.splitlines()[-1]))
        except Exception as e:
            out.append(f"save {d}: not fixed ({e})")
    return out


def restore_isolation(layer=None):
    """Put the original modsettings.lsx back (and re-enable the layer if it was added meanwhile). Safe to call when not isolated."""
    state = isolation_state()
    if not state:
        return []
    cfg, _, _, _ = mod_info(state.get("layer") or layer)
    _, _, ms = _paths(cfg)
    lines = []
    if os.path.exists(state["backup"]):
        shutil.copy2(state["backup"], ms)
        lines.append("restored the original mod list (modsettings.lsx)")
    try:
        os.remove(ISOLATION)
    except OSError:
        pass
    try:
        lines += enable(state["layer"])
    except Exception as e:  # restoring is the point; a failed re-enable is only a note
        lines.append(f"note: couldn't re-check {state['layer']} in modsettings.lsx ({e})")
    return lines


def _changed(path):
    """{file: content hash} of the files differing from git HEAD (ignoring line endings) in a mod folder."""
    import hashlib
    import subprocess
    r = subprocess.run(["git", "diff", "HEAD", "--ignore-cr-at-eol", "--name-only"], cwd=path, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    out = {}
    for f in r.stdout.split():
        p = os.path.join(path, f)
        out[f] = hashlib.sha1(open(p, "rb").read()).hexdigest() if os.path.exists(p) else None
    return out


def regen(m):
    """Run the layer's `regen` commands (layers.json) before packing and report generated files they changed: a
    generator run on its own overwrites what later generators add (2026-10-06: gen_subclass_features alone reset Warlock 17's
    spell pick and 28 icons, and the pak shipped that way)."""
    import subprocess
    cmds = m.get("regen") or []
    if not cmds:
        return []
    before = _changed(m["path"])
    lines = []
    for c in cmds:
        r = subprocess.run(c, shell=True, cwd=m["path"], capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            tail = (r.stdout + r.stderr).strip().splitlines()[-6:]
            raise RuntimeError(f"regen `{c}` failed (exit {r.returncode}) - not packed:\n  " + "\n  ".join(tail))
    after = _changed(m["path"])
    if before is None or after is None:
        return [f"regen ran ({len(cmds)} command(s)); not a git folder, drift not checked"]
    drift = sorted(f for f in after if before.get(f, "-") != after[f]) + sorted(f for f in before if f not in after)
    if not drift:
        return ["regen: generated files up to date"]
    return [f"!! regen changed {len(drift)} file(s) - they were stale (a generator run alone, or a hand edit to generated "
            f"output); the pak has the regenerated version - review and commit:"] + [f"     {f}" for f in drift[:20]]


def deploy(layer, do_enable=True):
    cfg, m, info, _ = mod_info(layer)
    _, mods_dir, _ = _paths(cfg)
    try:
        lines = regen(m)
    except RuntimeError as e:
        return [str(e)], False
    pak, n = pack(layer)
    lines.append(f"packed {n} files -> {pak}")
    if game_running():
        return lines + ["NOT DEPLOYED: the game is running and holds the pak open (close it, or use bg3_game_restart)"], False
    os.makedirs(mods_dir, exist_ok=True)
    target = os.path.join(mods_dir, info["Folder"] + ".pak")
    if os.path.exists(target):
        backup = os.path.join(os.path.dirname(pak), time.strftime("previous_%Y%m%d_%H%M%S.pak", time.localtime(os.path.getmtime(target))))
        shutil.copy2(target, backup)
    for other in glob.glob(os.path.join(mods_dir, f"*{info['UUID']}*.pak")):  # the same mod under an old Folder
        if os.path.normcase(other) != os.path.normcase(target):                  # name (renamed mod): two paks with
            moved = os.path.join(os.path.dirname(pak), "renamed_" + os.path.basename(other))  # one UUID would clash
            shutil.move(other, moved)
            lines.append(f"moved the old-name pak {os.path.basename(other)} out of the Mods folder -> {moved}")
    shutil.copy2(pak, target)
    lines.append(f"deployed {target}")
    for mgr in platform.mod_managers(cfg):
        if info["Folder"] + ".pak" in (mgr.get("managed") or []):
            lines.append(f"note: {mgr['name']} also manages this pak - its next deploy may replace it")
    if do_enable:
        lines += enable(layer)
    return lines, True


def environment():
    """Everything discovered about this machine, for bg3_environment."""
    out = [f"platform: {'Windows' if platform.IS_WINDOWS else 'WSL' if platform.IS_WSL else 'other'}"]
    try:
        cfg = sources.load_config()
    except sources.ConfigError as e:
        return "\n".join(out + [f"config: {e}"])
    gd = cfg["base"]["game_data"]
    out.append(f"game: {os.path.dirname(gd)}" + (f" ({cfg['base'].get('_store')}, discovered)" if cfg["base"].get("_store") else " (layers.json)"))
    exe = platform.game_exe(os.path.dirname(gd))
    out.append(f"game exe: {exe or 'not found'}")
    out.append(f"Divine.exe: {cfg['divine']}")
    ld = platform.larian_dir(cfg)
    out.append(f"Larian user folder: {ld}")
    _, mods_dir, ms = _paths(cfg)
    paks = glob.glob(os.path.join(mods_dir, "*.pak"))
    out.append(f"user Mods folder: {len(paks)} paks; modsettings.lsx: {ms} ({'found' if os.path.exists(ms) else 'missing'})")
    se_dir = os.path.join(ld, "Script Extender Logs")
    bin_dir = os.path.join(os.path.dirname(gd), "bin")
    out.append("Script Extender: " + ("installed" if os.path.exists(os.path.join(bin_dir, "DWrite.dll")) else "DWrite.dll not found in bin")
               + (f", logs in {se_dir}" if os.path.isdir(se_dir) else ", no log folder yet (enable logging in ScriptExtenderSettings.json)"))
    for mgr in platform.mod_managers(cfg):
        out.append(f"mod manager: {mgr['name']}" + (f" ({len(mgr['managed'])} paks)" if mgr.get("managed") else "") + f" - {mgr['note']}")
    out.append(f"index cache: {sources.CACHE}")
    for m in cfg["mods"]:
        try:
            _, _, info, deps = mod_info(m["name"])
            enabled = os.path.exists(ms) and info["UUID"] in open(ms, encoding="utf-8").read()
            deployed = os.path.exists(os.path.join(mods_dir, info["Folder"] + ".pak"))
            out.append(f"layer {m['name']}: {info['Name']} - pak {'deployed' if deployed else 'not in Mods'}, {'enabled' if enabled else 'not enabled'}")
        except (ValueError, OSError) as e:
            out.append(f"layer {m['name']}: {e}")
    return "\n".join(out)
