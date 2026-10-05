"""Dependency drift: track which release of each dependency a mod was built against, see what changed when the
dependency updates, and run the mod's own regeneration to follow it.

A dependency is any module in the mod's meta.lsx Dependencies that is also a configured layer (normally the .pak the
game loads - see README). Three versions are compared:
  declared  - Version64 (and MD5) in the mod's meta.lsx: what the mod tells the game it needs
  locked    - <mod>/bg3deps.lock.json: the release the mod was last synced to (with a fingerprint snapshot)
  current   - the dependency layer as indexed now (the deployed pak)
Snapshots (a hash per stats entry / template / progression / list / static-data node) live in the cache, so the
lock file stays small. bg3_deps_diff compares the locked snapshot with the current index and flags what the mod
overrides or references; bg3_deps_update runs the mod's `regen` commands (layers.json), re-indexes, lints, bumps
meta.lsx and rewrites the lock.
"""
import glob
import hashlib
import json
import os
import re
import subprocess
import time

from . import deploy, index, sources

LOCK = "bg3deps.lock.json"
BUILTIN = re.compile(r"^(Gustav|GustavDev|GustavX|Honour|HonourX|Shared|SharedDev|Engine|Game|MainUI|ModBrowser|PhotoMode|"
                     r"CrossplayUI|DiceSet_\d+)$")
SNAPS = os.path.join(sources.CACHE, "deps")
TOKEN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[A-Za-z_][A-Za-z0-9_]{2,}")


def version_str(v64):
    v = int(v64 or 0)
    return f"{v >> 55}.{(v >> 47) & 0xFF}.{(v >> 31) & 0xFFFF}.{v & 0x7FFFFFFF}"


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _mod(cfg, layer):
    m = next((x for x in cfg["mods"] if x["name"] == layer), None)
    if not m:
        raise ValueError(f"unknown mod layer {layer!r}")
    return m


def layer_meta(cfg, m):
    """(ModuleInfo, deps) of a mod layer - a folder, or a .pak read from its extracted cache copy."""
    root = sources.mod_root(cfg, m)
    metas = glob.glob(os.path.join(root, "Mods", "*", "meta.lsx"))
    return deploy.parse_meta(metas[0]) if len(metas) == 1 else (None, [])


def dependencies(cfg, layer):
    """[(declared ModuleShortDesc, dependency layer cfg or None, its ModuleInfo or None)] for a mod layer."""
    _, declared = layer_meta(cfg, _mod(cfg, layer))
    by_uuid = {}
    for m in cfg["mods"]:
        if m["name"] == layer:
            continue
        try:
            info, _ = layer_meta(cfg, m)
        except Exception:  # an unreadable layer is not a dependency we can track
            continue
        if info and info.get("UUID"):
            by_uuid[info["UUID"]] = (m, info)
    return [(d, *by_uuid.get(d["UUID"], (None, None))) for d in declared]


def fingerprint(db, layer):
    """{key: short hash} of everything a layer defines (key = 'stats:Name', 'tpl:<mapkey>', 'prog:<uuid>'...)."""
    fp = {}
    acc = {}
    for name, typ, using, data in db.execute("SELECT name, type, using_, data FROM stats WHERE layer=? ORDER BY rank", (layer,)):
        acc.setdefault("stats:" + name, []).append(f"{typ}|{using}|{data}")
    for mk, name, parent, attrs in db.execute("SELECT mapkey, name, parent, attrs FROM templates WHERE layer=? ORDER BY rank", (layer,)):
        acc.setdefault("tpl:" + mk, []).append(f"{name}|{parent}|{attrs}")
    for u, attrs in db.execute("SELECT uuid, attrs FROM prog WHERE layer=? ORDER BY rank", (layer,)):
        acc.setdefault("prog:" + u, []).append(attrs)
    for u, attrs in db.execute("SELECT uuid, attrs FROM lists WHERE layer=? ORDER BY rank", (layer,)):
        acc.setdefault("list:" + u, []).append(attrs)
    for kind, u, attrs in db.execute("SELECT kind, uuid, attrs FROM staticdata WHERE layer=? ORDER BY rank", (layer,)):
        acc.setdefault(f"static:{kind}:{u}", []).append(attrs)
    for k, parts in acc.items():
        fp[k] = hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:16]
    return fp


def _labels(db, layer):
    """Readable names for template / progression / list / static keys (stats keys are already names)."""
    lab = {}
    for mk, name in db.execute("SELECT mapkey, name FROM templates WHERE layer=?", (layer,)):
        lab["tpl:" + mk] = name
    for u, name, lvl in db.execute("SELECT uuid, name, level FROM prog WHERE layer=?", (layer,)):
        lab["prog:" + u] = f"{name} L{lvl}"
    for u, node, name in db.execute("SELECT uuid, node, name FROM lists WHERE layer=?", (layer,)):
        lab["list:" + u] = f"{node} {name}"
    for kind, u, name in db.execute("SELECT kind, uuid, name FROM staticdata WHERE layer=?", (layer,)):
        lab[f"static:{kind}:{u}"] = f"{kind} {name}"
    return lab


def snapshot(db, cfg, dep_layer):
    """Save the dependency layer's current fingerprint; returns the lock record for it."""
    m = _mod(cfg, dep_layer)
    info, _ = layer_meta(cfg, m)
    path = m["path"]
    md5 = _md5(path) if path.lower().endswith(".pak") and os.path.exists(path) else ""
    sig = md5 or hashlib.sha1(json.dumps(sources.mod_signature(cfg, m)[0]).encode()).hexdigest()
    snap_id = f"{dep_layer}-{info['Version64']}-{sig[:10]}"
    os.makedirs(SNAPS, exist_ok=True)
    out = os.path.join(SNAPS, snap_id + ".json")
    if not os.path.exists(out):
        json.dump({"layer": dep_layer, "fp": fingerprint(db, dep_layer), "labels": _labels(db, dep_layer),
                   "made": time.time()}, open(out, "w"))
    real = os.path.realpath(path)
    return {"layer": dep_layer, "name": info["Name"], "version64": info["Version64"],
            "version": version_str(info["Version64"]), "md5": md5, "source": real if real != path else path,
            "snapshot": snap_id, "locked_at": sources.iso(time.time())}


def _lock_path(cfg, layer):
    return os.path.join(_mod(cfg, layer)["path"], LOCK)


def read_lock(cfg, layer):
    p = _lock_path(cfg, layer)
    return json.load(open(p)) if os.path.exists(p) else {"deps": {}}


def write_lock(cfg, layer, lock):
    with open(_lock_path(cfg, layer), "w", newline="\n") as f:
        json.dump(lock, f, indent=2, sort_keys=True)
        f.write("\n")


def _current(cfg, dep_m, dep_info):
    path = dep_m["path"]
    md5 = _md5(path) if path.lower().endswith(".pak") and os.path.exists(path) else ""
    return {"version64": dep_info["Version64"], "version": version_str(dep_info["Version64"]), "md5": md5}


def status(store, layer):
    cfg = sources.load_config()
    lock = read_lock(cfg, layer)
    lines, untracked = [f"Dependencies of {layer} (declared in meta.lsx; locked in {LOCK}; current = indexed layer):"], []
    for d, dep_m, dep_info in dependencies(cfg, layer):
        if not dep_m:
            if not BUILTIN.match(d.get("Folder", "")):  # Larian's own modules ship with the game (base layer)
                untracked.append(d.get("Name") or d["UUID"])
            continue
        cur = _current(cfg, dep_m, dep_info)
        lk = lock["deps"].get(d["UUID"])
        flags = []
        if d.get("Version64") != cur["version64"]:
            flags.append("meta.lsx declares an older/other version")
        if d.get("MD5") and cur["md5"] and d["MD5"] != cur["md5"]:
            flags.append("meta.lsx MD5 differs from the pak")
        if not lk:
            flags.append("no lock yet - run bg3_deps_lock to set the baseline")
        elif (lk["md5"] or lk["version64"]) != (cur["md5"] or cur["version64"]):
            flags.append(f"DRIFT since lock ({lk['version']}, {lk['locked_at']})")
        lines.append(f"  {dep_m['name']}: {d.get('Name')} - declared {version_str(d.get('Version64'))}, "
                     f"locked {lk['version'] if lk else '-'}, current {cur['version']} [{os.path.basename(os.path.dirname(os.path.realpath(dep_m['path'])))}]")
        lines += [f"     !! {f}" for f in flags] or ["     up to date"]
    if untracked:
        lines.append(f"  not tracked (published mods with no layer - add their pak with bg3_add_mod_layer): {', '.join(untracked)}")
    return "\n".join(lines)


def _mod_tokens(db, layer):
    """Every identifier / GUID the mod layer defines or mentions, plus what it overrides (same key as the dep)."""
    toks = set()
    for name, using, data in db.execute("SELECT name, using_, data FROM stats WHERE layer=?", (layer,)):
        toks.add(name)
        if using:
            toks.add(using)
        toks.update(TOKEN.findall(data))
    for table, cols in (("templates", "mapkey, parent, attrs"), ("prog", "uuid, table_uuid, attrs"),
                        ("lists", "uuid, name, attrs"), ("staticdata", "uuid, name, attrs")):
        for row in db.execute(f"SELECT {cols} FROM {table} WHERE layer=?", (layer,)):
            for v in row:
                if v:
                    toks.update(TOKEN.findall(str(v)))
    return toks


def _own_keys(db, layer):
    return set(fingerprint(db, layer))


def diff(store, layer, dep_layer=None, limit=60):
    """What changed in each dependency since the lock, and which of those changes the mod overrides or references."""
    cfg = sources.load_config()
    lock = read_lock(cfg, layer)
    db = store.db
    out = []
    mod_tokens, own = _mod_tokens(db, layer), _own_keys(db, layer)
    for d, dep_m, dep_info in dependencies(cfg, layer):
        if not dep_m or (dep_layer and dep_m["name"] != dep_layer):
            continue
        lk = lock["deps"].get(d["UUID"])
        snap = os.path.join(SNAPS, (lk or {}).get("snapshot", "") + ".json")
        if not lk or not os.path.exists(snap):
            out.append(f"{dep_m['name']}: no locked snapshot to compare with - bg3_deps_lock sets the baseline")
            continue
        old = json.load(open(snap))
        new_fp, new_lab = fingerprint(db, dep_m["name"]), _labels(db, dep_m["name"])
        labels = {**old.get("labels", {}), **new_lab}
        added = sorted(set(new_fp) - set(old["fp"]))
        removed = sorted(set(old["fp"]) - set(new_fp))
        changed = sorted(k for k in set(new_fp) & set(old["fp"]) if new_fp[k] != old["fp"][k])
        cur = _current(cfg, dep_m, dep_info)
        out.append(f"{dep_m['name']}: locked {lk['version']} ({lk['locked_at']}) -> current {cur['version']}: "
                   f"{len(changed)} changed, {len(added)} added, {len(removed)} removed")

        def ident(k):
            return k.split(":")[-1]

        def show(k):
            lab = labels.get(k)
            return f"{k}" + (f" ({lab})" if lab else "")

        hits = []
        for kind, keys in (("changed", changed), ("removed", removed), ("added", added)):
            for k in keys:
                if k in own:
                    hits.append((0 if kind != "added" else 1, f"  {kind:7} OVERRIDDEN by {layer}: {show(k)}"))
                elif ident(k) in mod_tokens:
                    hits.append((1 if kind != "added" else 2, f"  {kind:7} referenced by {layer}: {show(k)}"))
        hits.sort()
        if hits:
            out.append(f" affects {layer} ({len(hits)}):")
            out += [h for _, h in hits[:limit]]
            if len(hits) > limit:
                out.append(f"  ... {len(hits) - limit} more")
        else:
            out.append(f" nothing {layer} overrides or references changed")
        by_type = {}
        for k in changed + added + removed:
            by_type[k.split(":")[0]] = by_type.get(k.split(":")[0], 0) + 1
        out.append(" all changes by kind: " + ", ".join(f"{t} {n}" for t, n in sorted(by_type.items())))
    return "\n".join(out) or f"{layer} has no tracked dependencies"


def lock_deps(store, layer):
    """Baseline: record every tracked dependency's current release + snapshot in the mod's lock file."""
    cfg = sources.load_config()
    lock = read_lock(cfg, layer)
    done = []
    for d, dep_m, _ in dependencies(cfg, layer):
        if dep_m:
            lock["deps"][d["UUID"]] = snapshot(store.db, cfg, dep_m["name"])
            done.append(f"{dep_m['name']} {lock['deps'][d['UUID']]['version']}")
    write_lock(cfg, layer, lock)
    return f"locked {', '.join(done) or 'nothing'} in {_lock_path(cfg, layer)}"


def bump_meta(cfg, layer, uuid, version64, md5):
    """Set the dependency's Version64 (and MD5, when the mod records one) in the mod's meta.lsx."""
    path = glob.glob(os.path.join(_mod(cfg, layer)["path"], "Mods", "*", "meta.lsx"))[0]
    text = open(path, encoding="utf-8").read()
    changed = []

    def fix(block):
        b = block.group(0)
        if f'value="{uuid}"' not in b:
            return b
        nb = re.sub(r'(<attribute id="Version64" type="int64" value=")[^"]*"', rf'\g<1>{version64}"', b)
        if md5:
            nb = re.sub(r'(<attribute id="MD5" type="LSString" value=")([^"]+)"', rf'\g<1>{md5}"', nb)
        if nb != b:
            changed.append(uuid)
        return nb

    text = re.sub(r'<node id="ModuleShortDesc">.*?</node>', fix, text, flags=re.S)
    if changed:
        open(path, "w", encoding="utf-8", newline="").write(text)
    return bool(changed)


def update(store, layer, apply=False, log=print):
    """Follow dependency drift: diff, run the mod's regen commands, re-index, lint, bump meta.lsx, rewrite the lock.
    apply=False only reports what would happen."""
    cfg = sources.load_config()
    m = _mod(cfg, layer)
    regen = m.get("regen") or []
    lock = read_lock(cfg, layer)
    drift = []
    for d, dep_m, dep_info in dependencies(cfg, layer):
        if not dep_m:
            continue
        cur = _current(cfg, dep_m, dep_info)
        lk = lock["deps"].get(d["UUID"])
        meta_old = d.get("Version64") != cur["version64"] or (d.get("MD5") and cur["md5"] and d["MD5"] != cur["md5"])
        if not lk or (lk["md5"] or lk["version64"]) != (cur["md5"] or cur["version64"]) or meta_old:
            drift.append((d, dep_m, cur))
    if not drift:
        return f"{layer}: every tracked dependency matches its lock and meta.lsx - nothing to update"
    out = [status(store, layer), "", diff(store, layer), ""]
    plan = [f"regen in {m['path']}: {c}" for c in regen] or ["(no `regen` commands configured for this layer in layers.json)"]
    plan += [f"re-index {layer}, lint stats + progressions",
             *[f"meta.lsx: {d.get('Name')} Version64 -> {cur['version64']} ({cur['version']})" for d, _, cur in drift],
             f"rewrite {LOCK}"]
    if not apply:
        return "\n".join(out + ["Plan (apply=True runs it):"] + [f"  {p}" for p in plan])
    out.append("Applying:")
    for c in regen:
        r = subprocess.run(c, shell=True, cwd=m["path"], capture_output=True, text=True, timeout=900)
        tail = (r.stdout + r.stderr).strip().splitlines()[-6:]
        out.append(f"  $ {c}  -> exit {r.returncode}")
        out += [f"      {t}" for t in tail]
        if r.returncode != 0:
            out.append("  !! regen failed - stopping before meta.lsx / lock changes")
            return "\n".join(out)
    index.refresh(store.db, cfg, force=[layer], log=log)
    store.invalidate()
    from . import lint, testing
    active = store.active(None)
    out.append("  " + lint.lint_stats(store, active, layer, limit=40).replace("\n", "\n  "))
    out.append("  " + testing.lint_progressions(store, active, layer).replace("\n", "\n  "))
    for d, dep_m, cur in drift:
        if bump_meta(cfg, layer, d["UUID"], cur["version64"], cur["md5"] if d.get("MD5") else ""):
            out.append(f"  meta.lsx: {d.get('Name')} -> {cur['version']}")
        lock["deps"][d["UUID"]] = snapshot(store.db, cfg, dep_m["name"])
    write_lock(cfg, layer, lock)
    out.append(f"  {LOCK} rewritten. Next: review the mod's git diff, deploy, and re-run its tests in game.")
    return "\n".join(out)


def fetch(store, layer, apply=False, wait_s=900, log=print):
    """Get each dependency's newest release from Nexus THROUGH VORTEX (its own Nexus login - no API key here), then update.

    For every dependency that is a layer and that Vortex deployed: find its Vortex mod (the deployment manifest names the
    staged mod a pak came from), ask Vortex to check Nexus for a newer file, and with apply=True: start the download
    (Nexus Premium: direct; a free account: Vortex opens the Nexus files page - click "Mod manager download" there), wait
    for it, install it as a new staged mod, switch the profile from the old version to the new one, deploy, and run
    update(apply=True) - diff, regen, re-index, lint, meta.lsx, lock. Needs the bridge (bg3_vortex_bridge_install)."""
    from . import vortex
    cfg = sources.load_config()
    st = vortex.action("status")
    if st.get("error"):
        return f"Vortex bridge: {st['error']}"
    mods = {m["id"]: m for m in st.get("mods") or []}
    out, todo = [], []
    for d, dep_m, dep_info in dependencies(cfg, layer):
        if not dep_m or not dep_m["path"].lower().endswith(".pak"):
            continue
        man = vortex.manifest(os.path.dirname(dep_m["path"])) or {}
        src = next((f["source"] for f in man.get("files") or [] if f.get("relPath") == os.path.basename(dep_m["path"])), None)
        if not src or src not in mods:
            out.append(f"  {dep_m['name']}: not deployed by Vortex ({os.path.basename(dep_m['path'])}) - fetch it yourself")
            continue
        chk = vortex.action("check-updates", src)
        if chk.get("error"):
            out.append(f"  {dep_m['name']}: Nexus update check failed: {chk['error']}")
            continue
        m = next((x for x in chk.get("mods") or [] if x["id"] == src), mods[src])
        nx = m.get("nexus") or {}
        have, new = m.get("version"), nx.get("newestVersion")
        if not new or new == have:
            out.append(f"  {dep_m['name']}: v{have} is the newest on Nexus (mod {nx.get('modId')})")
            continue
        out.append(f"  {dep_m['name']}: v{have} -> Nexus has v{new} (mod {nx.get('modId')}, file {nx.get('newestFileId')})")
        todo.append((dep_m, src, nx))
    head = [f"Dependencies of {layer} on Nexus (checked through Vortex):"] + out
    if not todo or not apply:
        return "\n".join(head + ([] if not todo else ["", "apply=True downloads, installs, switches, deploys and runs bg3_deps_update."]))
    for dep_m, src, nx in todo:
        r = vortex.action("update", src)
        if r.get("error"):
            head.append(f"  !! {dep_m['name']}: download failed to start: {r['error']}")
            return "\n".join(head)
        head.append(f"  {dep_m['name']}: download requested (a free Nexus account: click 'Mod manager download' on the page Vortex opened)")
        t0, dl = time.time(), None
        while time.time() - t0 < wait_s:
            for x in vortex.action("downloads").get("downloads") or []:
                # exactly the new file: an older download of the same mod (4.12.18.1 once) must never match
                if str(x.get("fileId")) == str(nx.get("newestFileId")) and str(x.get("modId")) == str(nx.get("modId")) \
                        and x.get("state") == "finished":
                    dl = x
            if dl:
                break
            time.sleep(10)
        if not dl:
            head.append(f"  !! {dep_m['name']}: no finished download after {wait_s}s - run bg3_deps_fetch(apply=True) again once it's in")
            return "\n".join(head)
        ins = vortex.action("install", dl["id"])
        if ins.get("error") or not ins.get("installed"):
            head.append(f"  !! install failed: {ins.get('error') or ins}")
            return "\n".join(head)
        new_id = ins["installed"]
        for step in (vortex.action("disable", src), vortex.action("enable", new_id), vortex.action("deploy")):
            if step.get("error"):
                head.append(f"  !! Vortex: {step['error']}")
                return "\n".join(head)
        head.append(f"  {dep_m['name']}: installed {new_id}, enabled instead of {src}, deployed")
    head.append("")
    head.append(update(store, layer, apply=True, log=log))
    return "\n".join(head)
