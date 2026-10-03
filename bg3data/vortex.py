"""Vortex (Nexus mod manager), read-only: what it has staged, what it has deployed into the game's Mods folder, and
its recent activity for the game - so "why isn't mod X in game" is one call instead of log archaeology.

Sources: the deployment manifest Vortex writes next to deployed files (Mods/vortex.deployment.json: staging path,
method, files and the staged mod each came from), the staging folder (one folder per installed mod/version) and
%APPDATA%/Vortex/vortex.log. Vortex's own state database (LevelDB) is never touched. Triggering a deploy needs a
Vortex-side bridge (an extension); until then a deploy is clicked in Vortex.
"""
import json
import os
import re
import time

from . import deploy, platform, sources

GAME_ID = "baldursgate3"
VERSION_TAIL = re.compile(r"[ -]\d[\d.\-]*(?: \d{4}-\d\d-\d\dT[\d-]+Z)?(?: [A-Za-z0-9]{6,12})?$")
LOG_KEEP = re.compile(r"Failed|ERRO|deployment \{|purge|install|extraction completed|remov", re.I)


def app_dir(cfg):
    v = (cfg.get("vortex") or {}).get("app_data")
    return platform.to_native(v) if v else os.path.join(platform.win_env("APPDATA") or "", "Vortex")


def manifest(mods_dir):
    p = os.path.join(mods_dir, "vortex.deployment.json")
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def staging_dir(cfg, man, log_lines):
    v = (cfg.get("vortex") or {}).get("staging")
    if v:
        return platform.to_native(v)
    if man and man.get("stagingPath"):
        return platform.to_native(man["stagingPath"])
    for ln in reversed(log_lines):  # downloads\<game>\x.zip sits beside the mods (staging) folder by default
        m = re.search(r'"archivePath":"([^"]+?)\\\\downloads\\\\' + GAME_ID, ln)
        if m:
            return platform.to_native(m.group(1).replace("\\\\", "\\") + "\\mods")
    return None


def _log_lines(adir, limit=4000):
    p = os.path.join(adir, "vortex.log")
    if not os.path.exists(p):
        return []
    with open(p, "rb") as f:
        f.seek(max(0, os.path.getsize(p) - 2_000_000))
        lines = f.read().decode("utf-8", "replace").splitlines()
    return lines[-limit:]


def status(cfg=None, log_n=12):
    cfg = cfg or sources.load_config()
    out = []
    running = "vortex.exe" in platform.tasklist()  # tasklist() is lower-cased
    adir = app_dir(cfg)
    try:
        _, mods_dir, _ = deploy._paths(cfg)
    except RuntimeError as e:
        return str(e)
    lines = _log_lines(adir)
    man = manifest(mods_dir)
    stage = staging_dir(cfg, man, lines)
    out.append(f"Vortex {'running' if running else 'not running'}; app data {adir}; staging {stage or '?'}")

    # deployed (manifest) vs what is in the Mods folder
    if man:
        files = man.get("files") or []
        t = man.get("deploymentTime")
        out.append(f"Deployed by Vortex into the Mods folder ({man.get('deploymentMethod', '?')}"
                   + (f", {sources.iso(t / 1000)}" if t else "") + f"): {len(files)} file(s)")
        for f in files:
            out.append(f"  {f.get('relPath')}  <- {f.get('source')}")
    else:
        out.append("Deployed by Vortex into the Mods folder: NOTHING (no vortex.deployment.json) - Vortex purged its "
                   "deployment or never deployed; click Deploy in Vortex")
    try:
        paks = sorted(f for f in os.listdir(mods_dir) if f.lower().endswith(".pak"))
    except OSError:
        paks = []
    managed = {(f.get("relPath") or "").lower() for f in (man or {}).get("files") or []}
    other = [p for p in paks if p.lower() not in managed]
    if other:
        out.append("Other paks in the Mods folder (not Vortex's): " + ", ".join(other))

    # staged mods, flagging several versions of one mod
    if stage and os.path.isdir(stage):
        entries = []
        for d in os.listdir(stage):
            full = os.path.join(stage, d)
            if os.path.isdir(full) and not d.startswith("__"):
                entries.append((VERSION_TAIL.sub("", d).strip() or d, d, os.path.getmtime(full)))
        groups = {}
        for base, d, mt in entries:
            groups.setdefault(base, []).append((mt, d))
        sources_deployed = {(f.get("source") or "") for f in (man or {}).get("files") or []}
        out.append(f"Staged mods: {len(entries)}")
        for base, vs in sorted(groups.items()):
            if len(vs) > 1:
                vs.sort()
                out.append(f"  !! {len(vs)} versions of '{base}' staged (enable one, remove the rest):")
                for mt, d in vs:
                    out.append(f"     {sources.iso(mt)}  {d}" + ("  [deployed]" if d in sources_deployed else ""))

    # recent activity for this game
    recent = [ln for ln in lines if LOG_KEEP.search(ln) and ("baldursgate3" in ln or "deployment {" in ln)]
    if recent:
        out.append(f"Recent Vortex activity ({os.path.join(adir, 'vortex.log')}):")
        for ln in recent[-log_n:]:
            ts = ln[:19].replace("T", " ")
            msg = re.sub(r"^\S+ \[\w+\] \[RENDERER\] ", "", ln)
            out.append(f"  {ts}Z {msg[:200]}")
    return "\n".join(out)
