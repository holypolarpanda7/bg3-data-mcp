"""Vortex (Nexus mod manager): what it has staged, what it has deployed into the game's Mods folder, and
its recent activity for the game - so "why isn't mod X in game" is one call instead of log archaeology.

Sources: the deployment manifest Vortex writes next to deployed files (Mods/vortex.deployment.json: staging path,
method, files and the staged mod each came from), the staging folder (one folder per installed mod/version) and
%APPDATA%/Vortex/vortex.log. Vortex's own state database (LevelDB) is never touched. Acting (deploy, enable, switching to
the dev profile) goes through the optional Vortex-side bridge extension, which runs Vortex's own actions.
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


# ---------------------------------------------------------------- optional bridge (a Vortex extension we ship)
BRIDGE_SRC = os.path.join(os.path.dirname(__file__), "integrations", "vortex_bridge")
BRIDGE_DIR_NAME = "bg3-data-mcp-bridge"


def bridge_dir(cfg=None):
    return os.path.join(app_dir(cfg or sources.load_config()), "plugins", BRIDGE_DIR_NAME)


def install_bridge(cfg=None):
    """Copy the bridge extension into Vortex's plugins folder (Vortex loads it on its next start)."""
    import shutil
    dest = bridge_dir(cfg)
    os.makedirs(dest, exist_ok=True)
    for f in ("index.js", "info.json"):
        shutil.copy2(os.path.join(BRIDGE_SRC, f), os.path.join(dest, f))
    running = "vortex.exe" in platform.tasklist()
    return (f"installed the bridge extension in {dest}. "
            + ("Restart Vortex to load it." if running else "It loads the next time Vortex starts.")
            + " It listens on 127.0.0.1 only and checks a token kept in bridge.json there.")


def bridge_call(method, endpoint, body=None, cfg=None, timeout=120):
    """Call the bridge; returns the decoded JSON (or {'error': ...})."""
    p = os.path.join(bridge_dir(cfg), "bridge.json")
    if not os.path.exists(p):
        return {"error": "bridge not running: install it with bg3_vortex_bridge_install and (re)start Vortex"}
    b = json.load(open(p))
    url = f"http://127.0.0.1:{b.get('port', 17846)}{endpoint}"
    data = json.dumps(body or {})
    if platform.IS_WSL:  # WSL2's 127.0.0.1 is not Windows' localhost: ask Windows to make the request
        import tempfile
        tmpdir = platform.to_native(platform.win_env("TEMP") or r"C:\Windows\Temp")
        fd, bodyfile = tempfile.mkstemp(suffix=".json", prefix="bg3bridge_", dir=tmpdir)  # a big body would overflow the command line
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data if method != "GET" else "")
            body_arg = "" if method == "GET" else (" -ContentType 'application/json; charset=utf-8' -Body ([IO.File]::ReadAllText('%s', "
                                                  "[Text.Encoding]::UTF8))" % platform.to_win(bodyfile))
            ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ErrorActionPreference='Stop'; try { $r = Invoke-RestMethod "
                  "-Uri '%s' -Method %s -TimeoutSec %d -Headers @{'x-bridge-token'='%s'}%s; $r | ConvertTo-Json -Depth 8 -Compress } "
                  "catch { @{error=$_.Exception.Message} | ConvertTo-Json -Compress }" % (url, method, timeout, b["token"], body_arg))
            r = platform.run_win(["powershell.exe", "-NoProfile", "-Command", ps], timeout=timeout + 15, encoding="utf-8", errors="replace")
        finally:
            try:
                os.remove(bodyfile)
            except OSError:
                pass
        try:
            return json.loads(r.stdout.strip() or "{}")
        except ValueError:
            return {"error": (r.stdout + r.stderr).strip()[:500]}
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, method=method, data=data.encode() if method != "GET" else None,
                                 headers={"x-bridge-token": b["token"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode() or "{}") or {"error": str(e)}
    except OSError as e:
        return {"error": f"bridge unreachable ({e}) - is Vortex running with the bridge installed?"}


def _update_profiles():
    """layers.json vortex.update_profiles: which profiles a dependency update the MCP installs applies to when Vortex asks -
    "current" (default: the active dev profile only, the play profile keeps its version) or "all"."""
    try:
        v = (sources.load_config().get("vortex") or {}).get("update_profiles", "current")
    except Exception:
        v = "current"
    return "all" if str(v).lower() == "all" else "current"


def action(name, mod=None, enabled=True):
    """deploy | purge | enable | disable | remove | status | check-updates | update | downloads | install (mod = the download id)
    through the bridge."""
    routes = {"status": ("GET", "/status", None), "deploy": ("POST", "/deploy", None), "purge": ("POST", "/purge", None),
              "enable": ("POST", "/enable", {"mod": mod, "enabled": True}),
              "disable": ("POST", "/enable", {"mod": mod, "enabled": False}),
              "remove": ("POST", "/remove", {"mod": mod}),
              "check-updates": ("POST", "/check-updates", {"mod": mod} if mod else {}),
              "update": ("POST", "/update", {"mod": mod, "profiles": _update_profiles()}),
              "downloads": ("GET", "/downloads", None), "loadorder": ("GET", "/loadorder", None),
              "install": ("POST", "/install", {"download": mod, "profiles": _update_profiles()}),
              "nexus-info": ("POST", "/nexus-info", {"modId": mod}),
              "nexus-download": ("POST", "/nexus-download", {"modId": mod, "profiles": _update_profiles()})}
    if name not in routes:  # (the token travels on a local PowerShell command line under WSL - local user only)
        return {"error": f"unknown action {name!r}; one of {sorted(routes)}"}
    if name in ("enable", "disable", "remove", "update", "install", "nexus-info", "nexus-download") and not mod:
        return {"error": f"{name} needs `mod` (a mod id or a unique part of it)"}
    method, ep, body = routes[name]
    return bridge_call(method, ep, body, timeout=300 if name in ("deploy", "purge", "install", "check-updates", "nexus-info") else 60)


def format_result(r):
    if not isinstance(r, dict) or r.get("error"):
        return f"bridge error: {(r or {}).get('error') if isinstance(r, dict) else r}"
    out = [f"{k}: {r[k]}" for k in ("removed", "mod", "enabled", "installed", "modId", "fileId", "note") if k in r]
    for d in r.get("downloads") or []:
        out.append(f"  download {d.get('id')}: {d.get('state')} {d.get('game')} v{d.get('version')} (Nexus mod {d.get('modId')} "
                   f"file {d.get('fileId')}) {os.path.basename(d.get('localPath') or '')}")
    if "gameId" in r:
        mods = r.get("mods") or []
        on = [m for m in mods if m.get("enabled")]
        out.append(f"Vortex {r['gameId']} profile '{r.get('profile')}': {len(on)}/{len(mods)} mods enabled"
                   + ("; DEPLOY NEEDED" if r.get("needToDeploy") else "; deployed"))
        for m in sorted(mods, key=lambda m: m.get("id") or ""):
            nx = m.get("nexus") or {}
            newer = nx.get("newestVersion") and nx.get("newestVersion") != m.get("version")
            out.append(f"  [{'x' if m.get('enabled') else ' '}] {m.get('id')}" + (f"  v{m['version']}" if m.get("version") else "")
                       + (f"  -> Nexus has v{nx['newestVersion']} (file {nx.get('newestFileId')})" if newer else ""))
    return "\n".join(out)


# ---------------------------------------------------------------- dev / test profile (2026-10-12)
# layers.json `vortex.dev_profile` (or a layer's own `vortex_profile`) names a Vortex profile kept for mod development and
# testing, so the play profile's mod list is never touched: bg3_deploy and the gate switch Vortex to it and fit it to exactly
# the layer's dependencies + test_mods (+ `vortex.dev_keep`), with every other pak mod disabled. The layer itself is deployed
# loose by bg3_deploy, so a Vortex-installed copy of it (e.g. the released version from Nexus) is disabled there too.
# Vortex mods that ship no game data (Script Extender's DWrite.dll, LSLib tools) are enabled: tests need the extender. Loose-file
# data mods (Generated/, Public/ overrides) count as data and are disabled like paks. Profile names match ignoring case/spaces/punctuation.
PROFILE_STATE = os.path.join(sources.CACHE, "vortex_profile.json")
DATA_FILE = re.compile(r"\.pak$|^(generated|public|mods|data|localization)/", re.I)


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def dev_profile_name(cfg, layer=None):
    m = next((x for x in cfg.get("mods") or [] if x["name"] == layer), {}) if layer else {}
    return m.get("vortex_profile") or (cfg.get("vortex") or {}).get("dev_profile")


def _uuid_to_mod(cfg, mods, files, stage=None):
    """{uuid: vortex mod id} for the staged mods: from every profile's BG3 load order file, then pak names in staging matched
    against the mods registry (Folder / Name). Several staged versions of one mod (an update installed for the current profile
    only): the one enabled in the active profile wins, else the newest staged folder - never an older version (2026-10-12)."""
    import glob
    cands = {}
    for u, mid in _uuid_candidates(cfg, mods, files):
        cands.setdefault(u, []).append(mid)

    def rank(mid):
        m = mods[mid]
        try:
            mt = os.path.getmtime(os.path.join(stage or "", m.get("installationPath") or mid))
        except OSError:
            mt = 0
        return (bool(m.get("enabled")), mt)
    return {u: max(dict.fromkeys(ms), key=rank) for u, ms in cands.items()}


def _uuid_candidates(cfg, mods, files):
    import glob
    out = []
    for f in glob.glob(os.path.join(app_dir(cfg), GAME_ID, "*_loadOrder.json")):
        try:
            for e in json.load(open(f, encoding="utf-8-sig")):
                u = ((e.get("data") or {}).get("uuid") or "").lower()
                if u and e.get("modId") in mods:
                    out.append((u, e["modId"]))
        except (OSError, ValueError):
            continue
    by_pak = {}
    for mid in mods:
        for p in (f for f in files.get(mid) or [] if f.lower().endswith(".pak")):
            by_pak.setdefault(os.path.splitext(os.path.basename(p))[0].lower(), []).append(mid)
    for u, d in deploy._registry().items():
        for k in (d.get("Folder"), d.get("Name")):
            if k and k.lower() in by_pak:
                out += [(u.lower(), mid) for mid in by_pak[k.lower()]]
    return out


def _files(stage, m):
    root = os.path.join(stage or "", m.get("installationPath") or m.get("id") or "")
    return [os.path.relpath(os.path.join(dp, f), root).replace("\\", "/") for dp, _, fs in os.walk(root) for f in fs]


def _save_state(d):
    os.makedirs(os.path.dirname(PROFILE_STATE), exist_ok=True)
    json.dump(d, open(PROFILE_STATE, "w", encoding="utf-8"), indent=1)


def fit_dev_profile(layer, apply=True, cfg=None):
    """Switch Vortex to the dev profile and fit it for `layer` (see above). Returns report lines; [] when no dev profile is
    configured. apply=False only reports what would change. Never raises for a Vortex problem: deploys carry on without it."""
    cfg = cfg or sources.load_config()
    name = dev_profile_name(cfg, layer)
    if not name:
        return []
    tag = f"Vortex dev profile '{name}'"
    if apply and deploy.game_running():
        return [f"{tag}: not switched - the game is running"]
    st = action("status")
    if st.get("error"):
        return [f"{tag}: not applied ({st['error']})"]
    profs = bridge_call("GET", "/profiles")
    if profs.get("error"):
        return [f"{tag}: not applied - the bridge is older than 0.3.0 (re-run bg3_vortex_bridge_install, restart Vortex)"]
    target = next((p for p in profs.get("profiles") or [] if _norm(p["name"]) == _norm(name)), None)
    mods = {m["id"]: m for m in st.get("mods") or []}
    man = manifest(deploy._paths(cfg)[1])
    stage = staging_dir(cfg, man, _log_lines(app_dir(cfg)))
    files = {mid: _files(stage, m) for mid, m in mods.items()} if stage else {}
    u2m = _uuid_to_mod(cfg, mods, files, stage)
    _, _, info, _ = deploy.mod_info(layer)
    keep = [str(k) for k in (cfg.get("vortex") or {}).get("dev_keep") or []]
    want_uuids = [u.lower() for u in deploy.desired_mods(layer) if u.lower() != info["UUID"].lower()]
    lines, want, missing = [], set(), []
    reg = deploy._registry()
    for u in want_uuids:
        mid = u2m.get(u)
        nm = (reg.get(u) or {}).get("Name") or u
        if mid:
            want.add(mid)
        elif os.path.exists(os.path.join(deploy._paths(cfg)[1], ((reg.get(u) or {}).get("Folder") or "?") + ".pak")):
            lines.append(f"  {nm}: not a Vortex mod (loose pak in the Mods folder) - left as is")
        else:
            missing.append(nm)
    seen, queue = set(), sorted(want)   # dependencies of dependencies, from each wanted mod's staged pak (meta.lsx)
    while queue:
        mid = queue.pop()
        if mid in seen:
            continue
        seen.add(mid)
        for pak in (f for f in files.get(mid) or [] if f.lower().endswith(".pak")):
            try:
                from . import vortex_order
                deps = vortex_order._pak_info(cfg, os.path.join(stage, mods[mid].get("installationPath") or mid, pak))["deps"]
            except Exception:
                continue
            for d in deps:
                u = d["UUID"].lower()
                if d.get("Folder") in deploy.BASE_MODULE_NAMES or d.get("Name") in deploy.BASE_MODULE_NAMES or u == info["UUID"].lower():
                    continue
                if u2m.get(u):
                    want.add(u2m[u])
                    queue.append(u2m[u])
                elif not os.path.exists(os.path.join(deploy._paths(cfg)[1], (d.get("Folder") or "?") + ".pak")):
                    missing.append(f"{d.get('Name') or u} (needed by {mods[mid].get('name') or mid})")
    for k in keep:
        hit = [mid for mid in mods if k.lower() in mid.lower()] or [mid for u, mid in u2m.items() if u == k.lower()]
        if len(hit) == 1:
            want.add(hit[0])
        else:
            lines.append(f"  dev_keep '{k}': {'ambiguous' if hit else 'no'} Vortex mod match - ignored")
    tools = {mid for mid in mods if files.get(mid) and not any(DATA_FILE.search(f) for f in files[mid])}
    want |= tools
    own = u2m.get(info["UUID"].lower())
    if own:
        want.discard(own)
    active = target and target["id"] == profs.get("active")
    on = {mid for mid, m in mods.items() if m.get("enabled")} if active else set()
    enable, disable = sorted(want - on), sorted(on - want)
    fitted = active and not enable and not disable
    if missing:
        lines.append(f"  !! not installed in Vortex: {', '.join(dict.fromkeys(missing))} - install them (Nexus) and re-run")
    if fitted:
        return [f"{tag}: active and fitted for {layer} ({len(want)} mods)"] + lines
    plan = (f"{tag}: {'create, ' if not target else ''}{'switch to, ' if not active else ''}enable {len(enable) if active else len(want)}"
            f"{f', disable {len(disable)}' if disable else ''} for {layer}: " + ", ".join(mods[m].get("name") or m for m in sorted(want)))
    if not apply:
        return [plan + " (dry run)"] + lines
    prev = profs.get("active")
    sw = r = bridge_call("POST", "/profile", {"profile": target["id"] if target else name, "create": True}, timeout=240)
    if r.get("error"):
        return [f"{tag}: switch failed ({r['error']})"] + lines
    if r.get("switched"):
        old = next((p["name"] for p in profs.get("profiles") or [] if p["id"] == prev), prev)
        _save_state({"previous": prev, "previous_name": old, "dev": name, "switched": time.strftime("%Y-%m-%d %H:%M:%S")})
        lines.insert(0, f"  switched Vortex from profile '{old}' (bg3_vortex_profile action='play' switches back)")
    on = {m["id"] for m in r.get("mods") or [] if m.get("enabled")}
    enable, disable = sorted(want - on), sorted(on - want)
    if enable or disable:
        r = bridge_call("POST", "/modstate", {"enable": enable, "disable": disable})
        if r.get("error"):
            return [f"{tag}: could not set the mod list ({r['error']})"] + lines
    from . import vortex_order
    res = vortex_order.report(apply=True, deploy_after=True).splitlines()
    return [f"{tag}: {'created, ' if sw.get('created') else ''}active; enabled {len(enable)}, disabled {len(disable)}; "
            f"sorted + deployed: {res[-1] if res else '?'}"] + lines


def switch_back():
    """Switch Vortex back to the profile that was active before the dev profile, and deploy it."""
    s = {}
    try:
        s = json.load(open(PROFILE_STATE, encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if not s.get("previous"):
        return "no previous profile recorded (the dev profile was never switched to by the MCP)"
    if deploy.game_running():
        return "the game is running - close it first"
    r = bridge_call("POST", "/profile", {"profile": s["previous"]}, timeout=240)
    if r.get("error"):
        return f"switch failed: {r['error']}"
    d = action("deploy")
    return f"switched Vortex back to '{s.get('previous_name')}' and deployed\n" + format_result(d)


def profiles_report(layer=None):
    cfg = sources.load_config()
    r = bridge_call("GET", "/profiles")
    if r.get("error"):
        return f"bridge error: {r['error']}"
    name = dev_profile_name(cfg, layer)
    out = [f"Vortex {GAME_ID} profiles (dev profile: {name or 'none - set vortex.dev_profile in layers.json'}):"]
    for p in r.get("profiles") or []:
        out.append(f"  {'*' if p['id'] == r.get('active') else ' '} {p['name']}  ({p['id']}, {p.get('enabled', 0)} mods enabled)"
                   + ("  [dev]" if name and _norm(p["name"]) == _norm(name) else ""))
    return "\n".join(out)
