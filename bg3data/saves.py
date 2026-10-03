"""Savegames: which mods a save was made with, compared with the current load order, and fixing that list.

A save (.lsv, an LSPK package) records its mods in meta.lsf (MetaData > ModuleSettings > Mods: Folder, Name, UUID,
Version64, MD5). A renamed or updated mod (same UUID), or a mod that is gone, makes the game warn or refuse the save.
fix_mods rewrites only that list - entries whose UUID is in the current modsettings.lsx take its Folder/Name/
Version64/MD5, and named mods can be dropped - then repacks the save with Divine. The original .lsv is copied to the
MCP cache (save_backups/) first. Dropping a mod that added content the save uses (items, spells, characters) can
break the save; dropping UI-only mods is safe.
"""
import json
import os
import re
import shutil
import tempfile
import time

from . import deploy, platform, sources
from .deps import BUILTIN  # Larian's own modules: the game owns their entries (modsettings holds placeholders)

MOD_NODE = re.compile(r'<node id="ModuleShortDesc">(?:(?!</node>).)*?</node>', re.S)
ATTR = re.compile(r'<attribute id="(\w+)" type="(\w+)" value="([^"]*)"\s*/>')
BACKUPS = os.path.join(sources.CACHE, "save_backups")


def save_root(cfg):
    ld = platform.larian_dir(cfg)
    profile = (cfg.get("game") or {}).get("profile", "Public")
    return os.path.join(ld, "PlayerProfiles", profile, "Savegames", "Story")


def list_saves(cfg, limit=10):
    root = save_root(cfg)
    out = []
    for d in os.listdir(root) if os.path.isdir(root) else []:
        full = os.path.join(root, d)
        lsv = [f for f in os.listdir(full) if f.endswith(".lsv")] if os.path.isdir(full) else []
        if lsv:
            p = os.path.join(full, lsv[0])
            out.append((os.path.getmtime(p), d, p))
    return sorted(out, reverse=True)[:limit]


def find_save(cfg, query):
    saves = list_saves(cfg, 10_000)
    if not query or query == "latest":
        if not saves:
            raise ValueError("no saves found")
        return saves[0][2]
    hits = [s for s in saves if query.lower() in s[1].lower()]
    if len(hits) != 1:
        raise ValueError(f"{len(hits)} saves match {query!r}" + (": " + ", ".join(h[1] for h in hits[:8]) if hits else ""))
    return hits[0][2]


def _divine(cfg, *args):
    return sources.divine(cfg, *args)


def read_meta(cfg, lsv, work):
    """Extract meta.lsf of a save into `work` and return its LSX text."""
    w = sources.winpath
    _divine(cfg, "-a", "extract-single-file", "-s", w(lsv), "-f", "meta.lsf", "-d", w(os.path.join(work, "meta.lsf")))
    _divine(cfg, "-a", "convert-resource", "-s", w(os.path.join(work, "meta.lsf")), "-d", w(os.path.join(work, "meta.lsx")))
    return open(os.path.join(work, "meta.lsx"), encoding="utf-8").read()


def mods_in(lsx):
    return [{k: v for k, _, v in ATTR.findall(n)} for n in MOD_NODE.findall(lsx)]


def current_mods(cfg):
    _, _, ms = deploy._paths(cfg)
    text = open(ms, encoding="utf-8").read()
    return {d.get("UUID"): d for d in ({k: v for k, _, v in ATTR.findall(n)} for n in MOD_NODE.findall(text))}


def compare(save_mods, cur):
    """[(save entry, problem or None, current entry)]"""
    rows = []
    for m in save_mods:
        if BUILTIN.match(m.get("Folder", "")):
            continue
        c = cur.get(m.get("UUID"))
        if not c:
            rows.append((m, "not in the current load order", None))
            continue
        diffs = [k for k in ("Folder", "Name", "Version64", "MD5") if c.get(k, "") and c.get(k, "") != m.get(k, "")]
        rows.append((m, ("differs: " + ", ".join(diffs)) if diffs else None, c))
    return rows


def describe(cfg, limit=5):
    cur = current_mods(cfg)
    out = []
    with tempfile.TemporaryDirectory(dir=platform.windows_temp()) as work:
        for mt, name, lsv in list_saves(cfg, limit):
            try:
                rows = compare(mods_in(read_meta(cfg, lsv, work)), cur)
            except Exception as e:  # an unreadable save: report and move on
                out.append(f"{name} ({sources.iso(mt)}): can't read meta.lsf ({e})")
                continue
            bad = [r for r in rows if r[1]]
            out.append(f"{name} ({sources.iso(mt)}): {len(rows)} mods" + (f", {len(bad)} mismatched" if bad else ", matches the load order"))
            for m, prob, _ in bad:
                out.append(f"   {m.get('Name') or m.get('Folder')} [{m.get('UUID')}]: {prob}")
    return "\n".join(out) or "no saves"


def fix_mods(cfg, query="latest", remove=(), sync=True, apply=False):
    lsv = find_save(cfg, query)
    cur = current_mods(cfg)
    remove = [r.lower() for r in remove]
    plan, out = [], [f"save: {lsv}"]
    with tempfile.TemporaryDirectory(dir=platform.windows_temp()) as work:
        lsx = read_meta(cfg, lsv, work)

        def edit(match):
            node = match.group(0)
            m = {k: v for k, _, v in ATTR.findall(node)}
            label = m.get("Name") or m.get("Folder")
            if any(r in (m.get("UUID", "").lower(), (m.get("Name") or "").lower(), (m.get("Folder") or "").lower())
                   or r in (m.get("Name") or "").lower() for r in remove):
                plan.append(f"remove {label} [{m.get('UUID')}]")
                return ""
            c = cur.get(m.get("UUID"))
            if sync and c and not BUILTIN.match(m.get("Folder", "")):
                for k in ("Folder", "Name", "Version64", "MD5"):
                    if k in c and c[k] != m.get(k):
                        plan.append(f"{label}: {k} {m.get(k)!r} -> {c[k]!r}")
                        node = re.sub(rf'(<attribute id="{k}" type="\w+" value=")[^"]*"', lambda x: x.group(1) + c[k] + '"', node)
            return node

        new = MOD_NODE.sub(edit, lsx)
        new = re.sub(r'\n\s*\n', '\n', new)
        if not plan:
            return "\n".join(out + ["nothing to change: the save's mod list already matches"])
        out += [f"  {p}" for p in plan]
        if not apply:
            return "\n".join(out + ["(dry run - apply=True rewrites the save; the original is backed up first)"])
        w = sources.winpath
        unpacked = os.path.join(work, "unpacked")
        _divine(cfg, "-a", "extract-package", "-s", w(lsv), "-d", w(unpacked))
        open(os.path.join(work, "meta.lsx"), "w", encoding="utf-8").write(new)
        _divine(cfg, "-a", "convert-resource", "-s", w(os.path.join(work, "meta.lsx")), "-d", w(os.path.join(unpacked, "meta.lsf")))
        packed = os.path.join(work, "new.lsv")
        _divine(cfg, "-a", "create-package", "-c", "lz4", "-s", w(unpacked), "-d", w(packed))
        chk = os.path.join(work, "chk")
        os.makedirs(chk)
        check = mods_in(read_meta(cfg, packed, chk))  # re-read the repacked save before replacing anything
        if not check:
            return "\n".join(out + ["!! the repacked save has no readable mod list - original left untouched"])
        os.makedirs(BACKUPS, exist_ok=True)
        backup = os.path.join(BACKUPS, time.strftime("%Y%m%d_%H%M%S_") + os.path.basename(lsv))
        shutil.copy2(lsv, backup)
        before = os.stat(lsv)
        shutil.copy2(packed, lsv)
        os.utime(lsv, (before.st_atime, before.st_mtime))  # keep its place in "newest save" (Continue loads that one)
        out.append(f"rewrote the save ({len(check)} mods now). Original backed up to {backup}")
    return "\n".join(out)
