"""Pack, deploy and enable a mod layer, for any mod and any setup (no per-mod scripts).

  pack      Mods/<folder> + Public/<folder> -> <folder>.pak with LSLib's Divine; checks meta.lsx is inside
  deploy    copies the pak into the user Mods folder (refuses while the game runs; backs up the old pak)
  enable    adds the mod to the active profile's modsettings.lsx after its dependencies (backup kept)

Mod managers: Vortex/BG3 Mod Manager may rewrite modsettings.lsx when they deploy or export a load order,
which disables a mod they don't manage; re-running enable (or bg3_deploy) puts it back. See bg3_environment.
"""
import glob
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
    text = open(metas[0], encoding="utf-8").read()
    info_block = re.search(r'<node id="ModuleInfo">(.*?)(?:<children>|</node>)', text, re.S)
    attrs = dict(re.findall(r'<attribute id="(\w+)" type="\w+" value="([^"]*)"', info_block.group(1) if info_block else ""))
    deps_block = re.search(r'<node id="Dependencies">(.*?)</node>\s*</children>', text, re.S)
    deps = []
    for blk in re.findall(r'<node id="ModuleShortDesc">(.*?)</node>', deps_block.group(1) if deps_block else "", re.S):
        d = dict(re.findall(r'<attribute id="(\w+)" type="\w+" value="([^"]*)"', blk))
        if d.get("UUID"):
            deps.append(d)
    folder = attrs.get("Folder") or os.path.basename(os.path.dirname(metas[0]))
    return cfg, m, {"Folder": folder, "Name": attrs.get("Name", folder), "UUID": attrs.get("UUID"),
                    "Version64": attrs.get("Version64", "36028797018963968"), "PublishHandle": attrs.get("PublishHandle", "0")}, deps


def _paths(cfg):
    ld = platform.larian_dir(cfg)
    if not ld:
        raise RuntimeError("Larian user folder not found (%LOCALAPPDATA%\\Larian Studios\\Baldur's Gate 3); set game.larian_dir in layers.json")
    profile = (cfg.get("game") or {}).get("profile", "Public")
    return ld, os.path.join(ld, "Mods"), os.path.join(ld, "PlayerProfiles", profile, "modsettings.lsx")


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


def deploy(layer, do_enable=True):
    cfg, m, info, _ = mod_info(layer)
    _, mods_dir, _ = _paths(cfg)
    lines = []
    pak, n = pack(layer)
    lines.append(f"packed {n} files -> {pak}")
    if game_running():
        return lines + ["NOT DEPLOYED: the game is running and holds the pak open (close it, or use bg3_game_restart)"], False
    os.makedirs(mods_dir, exist_ok=True)
    target = os.path.join(mods_dir, info["Folder"] + ".pak")
    if os.path.exists(target):
        backup = os.path.join(os.path.dirname(pak), time.strftime("previous_%Y%m%d_%H%M%S.pak", time.localtime(os.path.getmtime(target))))
        shutil.copy2(target, backup)
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
