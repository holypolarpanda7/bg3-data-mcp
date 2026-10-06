"""Locate and extract the source files for each layer.

Base layer: read straight from the installed game's paks (filtered extraction with LSLib's
Divine.exe), so it always reflects the current patch/hotfix. Mod layers: an unpacked mod
folder (Public/ + Mods/) or a .pak that gets extracted into the cache.
"""
import fnmatch
import glob
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone

from . import platform

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Under WSL the cache stays on the Linux filesystem: fast, and directory renames are reliable there (they
# fail on /mnt/* Windows drives while Divine.exe still holds handles). On Windows: %LOCALAPPDATA%.
# Override with BG3_DATA_CACHE.
CACHE = platform.cache_dir()
CONFIG = os.environ.get("BG3_DATA_CONFIG", os.path.join(ROOT, "layers.json"))
BASE_DEFAULTS = {
    "paks": ["Shared.pak", "Gustav.pak", "GustavX.pak", "Patch*.pak"],
    "module_order": ["Shared", "SharedDev", "Gustav", "GustavDev", "GustavX"],
    "exclude_modules": ["Honour", "HonourX", "PhotoMode"],
    "localization_pak": "Localization/English.pak",
}

BASE_GLOBS = [
    "Public/*/Stats/Generated/Data/*.txt",
    "Public/*/RootTemplates/_merged.lsf",
    "Public/*/Progressions/Progressions.lsf",
    "Public/*/Progressions/Progressions.lsx",
    "Public/*/Lists/*.lsf",
    "Public/*/Lists/*.lsx",
    "Public/*/MultiEffectInfos/*.lsf",
    "Public/*/Content/Assets/Effects/*_merged.lsf",
    "Public/*/ClassDescriptions/*.lsf", "Public/*/ClassDescriptions/*.lsx",
    "Public/*/Progressions/ProgressionDescriptions.lsf", "Public/*/Progressions/ProgressionDescriptions.lsx",
    "Public/*/Levelmaps/*.lsf", "Public/*/Levelmaps/*.lsx",
    "Public/*/ActionResourceDefinitions/*.lsf", "Public/*/ActionResourceDefinitions/*.lsx",
    "Public/*/Feats/*.lsf", "Public/*/Feats/*.lsx",
    "Mods/*/Scripts/thoth/helpers/*.khn",
]  # Divine's -x glob has no [..] classes, so list each extension


class ConfigError(RuntimeError):
    pass


_DISCOVERED = {}


def _discover(key, fn):
    if key not in _DISCOVERED:
        _DISCOVERED[key] = fn()
    return _DISCOVERED[key]


def load_config():
    """layers.json with every machine-specific path optional: missing ones are discovered, and Windows-style
    (D:\\...) or WSL-style (/mnt/d/...) paths both work. The raw file is kept for save_config."""
    raw = {}
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding="utf-8") as f:
            raw = json.load(f)
    cfg = json.loads(json.dumps(raw))
    cfg["_raw"] = raw
    base = cfg.setdefault("base", {})
    for k, v in BASE_DEFAULTS.items():
        base.setdefault(k, v)
    if base.get("game_data"):
        base["game_data"] = platform.to_native(base["game_data"])
    else:
        install, store = _discover("game", platform.find_game)
        if not install:
            raise ConfigError("Baldur's Gate 3 not found (Steam libraries, GOG, common folders); set base.game_data in layers.json")
        base["game_data"] = os.path.join(install, "Data")
        base["_store"] = store
    cfg["divine"] = platform.to_native(cfg["divine"]) if cfg.get("divine") else _discover("divine", lambda: platform.find_divine(raw))
    cfg.setdefault("mods", [])
    for m in cfg["mods"]:
        m["_raw_path"] = m["path"]
        m["path"] = platform.to_native(m["path"])
    if not cfg["divine"] or not os.path.isfile(cfg["divine"]):
        raise ConfigError(f"Divine.exe not found ({cfg.get('divine')!r}); install LSLib v1.20.4+ "
                          "(https://github.com/Norbyte/lslib/releases) and set 'divine' in layers.json or BG3_DIVINE")
    if not os.path.isdir(base["game_data"]):
        raise ConfigError(f"game Data folder not found: {base['game_data']!r} (layers.json base.game_data)")
    return cfg


def save_config(cfg):
    """Write back only what the user configured (discovered paths stay discovered; mod paths keep their spelling)."""
    out = json.loads(json.dumps(cfg.get("_raw") or {}))
    out["mods"] = []
    for m in cfg.get("mods", []):
        e = {k: v for k, v in m.items() if not k.startswith("_")}
        e["path"] = m.get("_raw_path") or m["path"]
        out["mods"].append(e)
    with open(CONFIG, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=2)
        f.write("\n")


def winpath(p):
    return platform.to_win(p)


def divine(cfg, *args):
    r = subprocess.run([cfg["divine"], "-g", "bg3", *args], capture_output=True, text=True)
    if r.returncode != 0 or "[FATAL]" in r.stdout or "[ERROR]" in r.stdout:
        raise RuntimeError(f"Divine {' '.join(args[:2])} failed: {r.stdout.strip()[-400:]} {r.stderr.strip()[-200:]}")
    return r.stdout


def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def _natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def base_paks(cfg):
    data = cfg["base"]["game_data"]
    out = []
    for pattern in cfg["base"]["paks"]:
        out += sorted(glob.glob(os.path.join(data, pattern)), key=lambda p: _natural(os.path.basename(p)))
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def base_signature(cfg):
    paks = base_paks(cfg) + [os.path.join(cfg["base"]["game_data"], cfg["base"]["localization_pak"])]
    stats = [(os.path.basename(p), int(os.path.getmtime(p)), os.path.getsize(p)) for p in paks]
    # the extraction globs are part of it: a newly indexed file kind re-extracts the base game (added 2026-10-06 with
    # ProgressionDescriptions, which no pak change would have pulled in)
    return json.dumps(stats + [("globs", sorted(BASE_GLOBS))]), max(s[1] for s in stats)


def extract_base(cfg, log=print):
    """Extract the indexable files from every base pak into cache/base/<pak>/ and convert to text."""
    final_root = os.path.join(CACHE, "base")
    out_root = os.path.join(CACHE, "base.tmp")
    if os.path.isdir(out_root):
        shutil.rmtree(out_root)
    os.makedirs(out_root)
    for pak in base_paks(cfg):
        dest = os.path.join(out_root, os.path.splitext(os.path.basename(pak))[0])
        listing = divine(cfg, "-a", "list-package", "-s", winpath(pak))
        names = [l.split("\t")[0] for l in listing.splitlines() if "\t" in l]
        wanted = [n for n in names if any(fnmatch.fnmatch(n, g) for g in BASE_GLOBS)]
        if not wanted:
            log(f"  {os.path.basename(pak)}: nothing to index")
            continue
        for g in BASE_GLOBS:
            if any(fnmatch.fnmatch(n, g) for n in wanted):
                divine(cfg, "-a", "extract-package", "-s", winpath(pak), "-d", winpath(dest), "-x", g)
        convert_lsf_tree(cfg, dest)
        log(f"  {os.path.basename(pak)}: {len(wanted)} files")
    loca_pak = os.path.join(cfg["base"]["game_data"], cfg["base"]["localization_pak"])
    loca_dir = os.path.join(out_root, "_loca")
    divine(cfg, "-a", "extract-package", "-s", winpath(loca_pak), "-d", winpath(loca_dir), "-x", "*english.loca")
    for f in glob.glob(os.path.join(loca_dir, "**", "*.loca"), recursive=True):
        divine(cfg, "-a", "convert-loca", "-s", winpath(f), "-d", winpath(f[:-5] + ".xml"))
    if os.path.isdir(final_root):
        shutil.rmtree(final_root)
    os.replace(out_root, final_root)
    return final_root


def convert_lsf_tree(cfg, root):
    """Batch-convert every .lsf under root to a sibling .lsx (one Divine call)."""
    if glob.glob(os.path.join(root, "**", "*.lsf"), recursive=True):
        divine(cfg, "-a", "convert-resources", "-s", winpath(root), "-d", winpath(root), "-i", "lsf", "-o", "lsx")


def module_of(path):
    """'.../Public/GustavDev/Stats/...' -> 'GustavDev'."""
    parts = path.replace("\\", "/").split("/")
    for key in ("Public", "Mods"):
        if key in parts:
            i = parts.index(key)
            if i + 1 < len(parts):
                return parts[i + 1]
    return "?"


STATIC_PATTERNS_LSX = ["Public/*/ClassDescriptions/*.lsx", "Public/*/Levelmaps/*.lsx",
                       "Public/*/ActionResourceDefinitions/*.lsx", "Public/*/Feats/*.lsx",
                       "Public/*/Progressions/ProgressionDescriptions.lsx"]


def base_files(cfg, kind):
    """Base-layer files of a kind in load order: (module label, path)."""
    root = os.path.join(CACHE, "base")
    order = cfg["base"]["module_order"]
    excl = set(cfg["base"]["exclude_modules"])
    pats = {
        "stats": "Public/*/Stats/Generated/Data/*.txt",
        "templates": "Public/*/RootTemplates/_merged.lsx",
        "progressions": "Public/*/Progressions/Progressions.lsx",
        "lists": "Public/*/Lists/*.lsx",
        "mei": "Public/*/MultiEffectInfos/*.lsx",
        "fxbanks": "Public/*/Content/Assets/Effects/**/_merged.lsx",
        "staticdata": STATIC_PATTERNS_LSX,
        "khn": "Mods/*/Scripts/thoth/helpers/*.khn",
    }
    found = []
    for pak_dir in [os.path.join(root, os.path.splitext(os.path.basename(p))[0]) for p in base_paks(cfg)]:
        pl = pats[kind] if isinstance(pats[kind], list) else [pats[kind]]
        for f in sorted(f for pat in pl for f in glob.glob(os.path.join(pak_dir, pat), recursive=True)):
            mod = module_of(f)
            if mod in excl:
                continue
            rank = order.index(mod) if mod in order else len(order)
            found.append((rank, os.path.basename(pak_dir), mod, f))
    # module order first, then pak order (hotfix paks after base paks for the same module)
    pak_rank = {os.path.splitext(os.path.basename(p))[0]: i for i, p in enumerate(base_paks(cfg))}
    found.sort(key=lambda t: (t[0], pak_rank.get(t[1], 999), t[3]))
    return [(f"base/{mod}" + ("" if pak in ("Shared", "Gustav", "GustavX") else f"@{pak}"), f) for _, pak, mod, f in found]


def base_loca_files():
    return sorted(glob.glob(os.path.join(CACHE, "base", "_loca", "**", "*.xml"), recursive=True))


# ---------------------------------------------------------------- mod layers
def mod_root(cfg, mod):
    """Folder holding Public/ + Mods/ for a mod layer; .pak layers are extracted into the cache."""
    path = mod["path"]
    if path.lower().endswith(".pak"):
        dest = os.path.join(CACHE, "mods", mod["name"])
        stamp = os.path.join(dest, ".source_mtime")
        mt = str(int(os.path.getmtime(path)))
        if not (os.path.exists(stamp) and open(stamp).read() == mt):
            if os.path.isdir(dest):
                shutil.rmtree(dest)
            divine(cfg, "-a", "extract-package", "-s", winpath(path), "-d", winpath(dest))
            open(stamp, "w").write(mt)
        return dest
    return path


def mod_files(cfg, mod, kind):
    root = mod_root(cfg, mod)
    pats = {
        "stats": ["Public/*/Stats/Generated/Data/*.txt"],
        "templates": ["Public/*/RootTemplates/*.lsx", "Public/*/RootTemplates/*.lsf"],
        "progressions": ["Public/*/Progressions/Progressions.lsx", "Public/*/Progressions/Progressions.lsf"],
        "lists": ["Public/*/Lists/*.lsx", "Public/*/Lists/*.lsf"],
        "loca": ["Mods/*/Localization/English/*.xml"],
        "mei": ["Public/*/MultiEffectInfos/*.lsx", "Public/*/MultiEffectInfos/*.lsf"],
        "fxbanks": ["Public/*/Content/Assets/Effects/**/_merged.lsx", "Public/*/Content/Assets/Effects/**/_merged.lsf"],
        "staticdata": STATIC_PATTERNS_LSX + [x[:-1] + "f" for x in STATIC_PATTERNS_LSX],
        "khn": ["Mods/*/Scripts/thoth/helpers/*.khn"],
    }
    files = []
    if not os.path.isdir(root):
        raise FileNotFoundError(f"mod layer '{mod['name']}': path not found: {root}")
    for p in pats[kind]:
        files += glob.glob(os.path.join(root, p), recursive=True)
    if kind in ("templates", "progressions", "lists", "mei", "fxbanks", "staticdata"):
        lsx = {f[:-4] for f in files if f.endswith(".lsx")}
        need = [f for f in files if f.endswith(".lsf") and f[:-4] not in lsx]
        if need:
            conv = os.path.join(CACHE, "mods", mod["name"] + "_lsx")
            stale = []
            for f in need:
                target = os.path.join(conv, os.path.relpath(f, root)[:-4] + ".lsx")
                if not os.path.exists(target) or os.path.getmtime(target) < os.path.getmtime(f):
                    stale.append(f)
                files.append(target)
            if stale:
                # stage the stale .lsf files and convert them in one batch
                stage = os.path.join(CACHE, "mods", mod["name"] + "_stage")
                if os.path.isdir(stage):
                    shutil.rmtree(stage)
                for f in stale:
                    dst = os.path.join(stage, os.path.relpath(f, root))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(f, dst)
                divine(cfg, "-a", "convert-resources", "-s", winpath(stage), "-d", winpath(conv), "-i", "lsf", "-o", "lsx")
                shutil.rmtree(stage)
        files = [f for f in files if f.endswith(".lsx")] if kind != "loca" else files
    return sorted(files)


def mod_signature(cfg, mod):
    path = mod["path"]
    if path.lower().endswith(".pak"):
        return json.dumps([int(os.path.getmtime(path)), os.path.getsize(path)]), os.path.getmtime(path)
    if not os.path.isdir(path):
        return json.dumps(["missing", path]), 0
    files = []
    for kind in ("stats", "loca"):
        files += mod_files(cfg, mod, kind)
    for pat in ["Public/*/RootTemplates/*.ls[fx]", "Public/*/Progressions/*.ls[fx]", "Public/*/Lists/*.ls[fx]",
                "Public/*/MultiEffectInfos/*.ls[fx]", "Public/*/Content/Assets/Effects/**/_merged.ls[fx]",
                "Public/*/ClassDescriptions/*.ls[fx]", "Public/*/Levelmaps/*.ls[fx]",
                "Public/*/ActionResourceDefinitions/*.ls[fx]", "Public/*/Feats/*.ls[fx]",
                "Mods/*/Scripts/thoth/helpers/*.khn"]:
        files += glob.glob(os.path.join(path, pat), recursive=True)
    stats = sorted((os.path.relpath(f, path), int(os.path.getmtime(f)), os.path.getsize(f)) for f in files)
    newest = max((s[1] for s in stats), default=0)
    return json.dumps(stats), newest
