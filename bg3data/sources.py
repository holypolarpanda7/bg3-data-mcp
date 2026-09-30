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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "cache")
CONFIG = os.path.join(ROOT, "layers.json")

BASE_GLOBS = [
    "Public/*/Stats/Generated/Data/*.txt",
    "Public/*/RootTemplates/_merged.lsf",
    "Public/*/Progressions/Progressions.lsf",
    "Public/*/Progressions/Progressions.lsx",
    "Public/*/Lists/*.lsf",
    "Public/*/Lists/*.lsx",
]  # Divine's -x glob has no [..] classes, so list each extension


def load_config():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


def winpath(p):
    return subprocess.check_output(["wslpath", "-w", p], text=True).strip()


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
    return json.dumps(stats), max(s[1] for s in stats)


def extract_base(cfg, log=print):
    """Extract the indexable files from every base pak into cache/base/<pak>/ and convert to text."""
    out_root = os.path.join(CACHE, "base")
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
    return out_root


def convert_lsf_tree(cfg, root):
    """Convert every .lsf under root to a sibling .lsx (skipping ones already converted)."""
    for f in glob.glob(os.path.join(root, "**", "*.lsf"), recursive=True):
        target = f[:-4] + ".lsx"
        if not os.path.exists(target) or os.path.getmtime(target) < os.path.getmtime(f):
            divine(cfg, "-a", "convert-resource", "-s", winpath(f), "-d", winpath(target))


def module_of(path):
    """'.../Public/GustavDev/Stats/...' -> 'GustavDev'."""
    parts = path.replace("\\", "/").split("/")
    for key in ("Public", "Mods"):
        if key in parts:
            i = parts.index(key)
            if i + 1 < len(parts):
                return parts[i + 1]
    return "?"


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
    }
    found = []
    for pak_dir in [os.path.join(root, os.path.splitext(os.path.basename(p))[0]) for p in base_paks(cfg)]:
        for f in sorted(glob.glob(os.path.join(pak_dir, pats[kind]))):
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
    }
    files = []
    for p in pats[kind]:
        files += glob.glob(os.path.join(root, p))
    if kind in ("templates", "progressions", "lists"):
        lsx = {f[:-4] for f in files if f.endswith(".lsx")}
        need = [f for f in files if f.endswith(".lsf") and f[:-4] not in lsx]
        if need:
            conv = os.path.join(CACHE, "mods", mod["name"] + "_lsx")
            for f in need:
                rel = os.path.relpath(f, root)
                target = os.path.join(conv, rel[:-4] + ".lsx")
                if not os.path.exists(target) or os.path.getmtime(target) < os.path.getmtime(f):
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    divine(cfg, "-a", "convert-resource", "-s", winpath(f), "-d", winpath(target))
                files.append(target)
        files = [f for f in files if f.endswith(".lsx")] if kind != "loca" else files
    return sorted(files)


def mod_signature(cfg, mod):
    path = mod["path"]
    if path.lower().endswith(".pak"):
        return json.dumps([int(os.path.getmtime(path)), os.path.getsize(path)]), os.path.getmtime(path)
    files = []
    for kind in ("stats", "loca"):
        files += mod_files(cfg, mod, kind)
    for pat in ["Public/*/RootTemplates/*.ls[fx]", "Public/*/Progressions/*.ls[fx]", "Public/*/Lists/*.ls[fx]"]:
        files += glob.glob(os.path.join(path, pat))
    stats = sorted((os.path.relpath(f, path), int(os.path.getmtime(f)), os.path.getsize(f)) for f in files)
    newest = max((s[1] for s in stats), default=0)
    return json.dumps(stats), newest
