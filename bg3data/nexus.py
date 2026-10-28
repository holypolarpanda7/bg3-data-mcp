"""Nexus Mods publishing through the v3 API (open beta; the API behind the official Nexus-Mods/upload-action), 2026-10-07.

  python -m bg3data.nexus status LAYER                       the layer's Nexus mod and its files (ids, versions, categories)
  python -m bg3data.nexus upload LAYER ZIP --version V [--file-id ID] [--name N] [--changelog TEXT] [--keep-old] [--apply]
                                                              a new version of the layer's main file (dry run without --apply);
                                                              the changelog defaults to the layer's CHANGELOG.md "## vV" section

What the API can do: upload a file, add a version to an existing mod file (archiving the previous one), create a new file on an
existing mod, changelog entries, per-file-version requirements on other Nexus files. What it can't: create a mod page or edit
its title, description, images or page requirements - those stay on the website.

Config: the API key from NEXUS_API_KEY or ~/.config/bg3-data-mcp/nexus.env (NEXUS_API_KEY=...; create one at
https://www.nexusmods.com/settings/api-keys). A layer names its page in layers.json:
  "nexus": {"game": "baldursgate3", "mod": 20246, "file": "<v3 mod-file id of the main file, from `status`>", "keep_versions": 4}
keep_versions (default 1): how many of the newest versions stay downloadable. Above 1 an upload doesn't archive the previous
version; the API can't change an older version's category, so `upload` then applies the policy on the website (Files tab,
signed-in Edge window: `python -m bg3data.nexus_web retention LAYER --apply`) and `status` lists what is still out of policy.
Every call needs the key, reads included.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

API = os.environ.get("NEXUSMODS_API_BASE", "https://api.nexusmods.com/v3").rstrip("/")
ENV_FILE = os.path.expanduser("~/.config/bg3-data-mcp/nexus.env")
UA = "bg3-data-mcp/nexus (+https://github.com/holypolarpanda7/bg3-data-mcp)"


def api_key():
    k = os.environ.get("NEXUS_API_KEY")
    if not k and os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            if line.strip().startswith("NEXUS_API_KEY="):
                k = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not k:
        raise SystemExit(f"no Nexus API key: set NEXUS_API_KEY or put NEXUS_API_KEY=... in {ENV_FILE} "
                         "(https://www.nexusmods.com/settings/api-keys)")
    return k


def _req(method, url, body=None, headers=None, raw=False, timeout=60):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    h = {"User-Agent": UA, **(headers or {})}
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            payload = resp.read()
            return (resp, payload) if raw else (json.loads(payload) if payload else {})
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {url}: HTTP {e.code} - {e.read().decode(errors='replace')[:500]}") from None


def call(method, path, body=None):
    return _req(method, API + path, body, {"apikey": api_key(), "Content-Type": "application/json"})


def nexus_entry(layer):
    from . import testing
    m = testing.mod_entry(layer)
    nx = m.get("nexus") or {}
    if not nx.get("mod"):
        raise SystemExit(f'layer {layer!r} has no "nexus": {{"mod": ...}} in layers.json')
    return {"game": nx.get("game", "baldursgate3"), **nx}


def mod_info(layer):
    nx = nexus_entry(layer)
    mod = call("GET", f"/games/{nx['game']}/mods/{nx['mod']}")["data"]
    files = call("GET", f"/mods/{mod['id']}/files")["data"].get("mod_files", [])
    return nx, mod, files


ACTIVE = {"main", "update", "optional", "old_version", "miscellaneous"}


def versions(fid):
    """The versions of mod file `fid`, newest first: dicts with id, version, category, game_scoped_id."""
    v = call("GET", f"/mod-files/{fid}/versions")["data"]
    items = v.get("versions") or v.get("mod_file_versions") or [] if isinstance(v, dict) else v
    return sorted(items, key=lambda x: int(x.get("id") or 0), reverse=True)


def retention(nx, vers=None):
    """Lines saying which versions to archive / restore on the website so the newest `keep_versions` stay downloadable."""
    keep = int(nx.get("keep_versions") or 1)
    if not nx.get("file"):
        return []
    vers = vers if vers is not None else versions(nx["file"])
    out = [f"  keep the newest {keep} version(s) downloadable (layers.json nexus.keep_versions):"]
    todo = 0
    for i, x in enumerate(vers):
        cat = x.get("category")
        want = "keep" if i < keep else "archive"
        mark = ""
        if want == "keep" and cat not in ACTIVE:
            mark, todo = "  <- RESTORE on the website (Files tab: move out of Archived, e.g. to Old versions)", todo + 1
        elif want == "archive" and cat in ACTIVE:
            mark, todo = "  <- ARCHIVE on the website (Files tab)", todo + 1
        out.append(f"    {x.get('version'):<10} {cat:<13} (file id {x.get('game_scoped_id')}){mark}")
    out.append("    nothing to change" if not todo else f"    {todo} change(s) to make on the website - the v3 API can't set a version's category")
    return out


def status(layer):
    nx, mod, files = mod_info(layer)
    out = [f"{layer}: Nexus {nx['game']} mod {nx['mod']} = v3 mod {mod['id']} - {mod.get('name')!r}"]
    for f in files:   # the v3 file record: id, name, is_active, last_file_uploaded_at, versions/archived/removed counts
        out.append(f"  file {f.get('id')}  {f.get('name')!r}  {'active' if f.get('is_active') else 'inactive'}  "
                   f"last upload {f.get('last_file_uploaded_at')}  versions {f.get('versions_count')} "
                   f"(archived {f.get('archived_count')})" + ("   <- layers.json nexus.file" if str(f.get('id')) == str(nx.get('file')) else ""))
    if not nx.get("file"):
        out.append('  -> set "file" (the main file\'s id above) under "nexus" in layers.json before uploading')
    out += retention(nx)
    return "\n".join(out)


def _upload(path, log):
    size = os.path.getsize(path)
    up = call("POST", "/uploads/multipart", {"filename": os.path.basename(path), "size_bytes": str(size)})["data"]
    urls, part = up["part_presigned_urls"], int(up["part_size_bytes"])
    log(f"upload {up['id']}: {len(urls)} part(s) of {part} bytes")
    etags = []
    with open(path, "rb") as fh:
        for n, url in enumerate(urls, 1):
            fh.seek((n - 1) * part)
            chunk = fh.read(part)
            resp, _ = _req("PUT", url, chunk, {"Content-Type": "application/octet-stream"}, raw=True, timeout=600)
            etags.append((n, resp.headers.get("ETag", "").replace('"', "")))
    xml = "<CompleteMultipartUpload>" + "".join(f"<Part><PartNumber>{n}</PartNumber><ETag>{e}</ETag></Part>" for n, e in etags) \
        + "</CompleteMultipartUpload>"
    _req("POST", up["complete_presigned_url"], xml.encode(), {"Content-Type": "application/xml"}, raw=True)
    call("POST", f"/uploads/{up['id']}/finalise")
    for i in range(60):
        st = call("GET", f"/uploads/{up['id']}")["data"]
        if st.get("state") == "available":
            return up["id"]
        time.sleep(min(2 * 1.5 ** i, 30))
    raise RuntimeError(f"upload {up['id']} never became available")


def changelog_from_file(layer, version):
    """The body of the "## vVERSION ..." section of the layer's CHANGELOG.md, or None."""
    from . import testing
    cl = os.path.join(testing.mod_entry(layer)["path"], "CHANGELOG.md")
    if not os.path.exists(cl):
        return None
    sec = re.search(rf"^## v{re.escape(version)}\b[^\n]*\n(.*?)(?=\n## |\Z)", open(cl, encoding="utf-8").read(), re.S | re.M)
    return sec.group(1).strip() or None if sec else None


def upload(layer, zip_path, version, file_id=None, name=None, changelog=None, keep_old=False, apply=False, log=print):
    """A new version of the layer's main Nexus file. Dry run unless apply=True: it shows the mod, the file it would update and
    what it would send, and changes nothing."""
    if not os.path.isfile(zip_path):
        raise SystemExit(f"no such file: {zip_path}")
    nx, mod, files = mod_info(layer)
    fid = file_id or nx.get("file")
    target = next((f for f in files if str(f.get("id")) == str(fid)), None)
    if not target:
        raise SystemExit(f"file {fid!r} isn't one of mod {nx['mod']}'s files - run `status {layer}` and set nexus.file")
    changelog = changelog or changelog_from_file(layer, version)
    body = {"name": name or target.get("name") or os.path.basename(zip_path), "version": version, "file_category": "main",
            "archive_existing_file": not keep_old and int(nx.get("keep_versions") or 1) <= 1,
            "update_mod_version": True, "primary_mod_manager_download": True}
    log(f"{'UPLOAD' if apply else 'DRY RUN'}: {os.path.basename(zip_path)} ({os.path.getsize(zip_path)} bytes) -> mod {nx['mod']} "
        f"({mod.get('name')!r}) file {fid} ({target.get('name')!r}) as version {version}"
        + ("; the previous version is archived" if body["archive_existing_file"] else "; the previous version stays downloadable"))
    log(f"  version request: {json.dumps(body)}")
    if changelog:
        log(f"  changelog {version}: {changelog[:200]}")
    else:
        log(f"  WARNING: no changelog - pass --changelog or add a '## v{version}' section to CHANGELOG.md")
    if not apply:
        return None
    uid = _upload(zip_path, log)
    ver = call("POST", f"/mod-files/{fid}/versions", {**body, "upload_id": uid})["data"]
    log(f"  created version {ver.get('version', {}).get('id') if isinstance(ver.get('version'), dict) else ver}")
    if changelog:
        call("POST", f"/mods/{mod['id']}/changelogs", {"version": version, "changelog": changelog})
        log("  changelog added")
    if int(nx.get("keep_versions") or 1) > 1:   # the website half: archive what fell out of the newest N (signed-in Edge window)
        try:
            from . import nexus_web
            log("  " + nexus_web.apply_retention(layer, apply=True).replace("\n", "\n  "))
        except Exception as e:   # signed out / no Edge window: say what's left to do
            log(f"  retention on the website not applied ({e}); run: python -m bg3data.nexus_web retention {layer} --apply")
    for line in retention(nx):
        log(line)
    return ver


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m bg3data.nexus")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("layer")
    u = sub.add_parser("upload")
    u.add_argument("layer")
    u.add_argument("zip")
    u.add_argument("--version", required=True)
    u.add_argument("--file-id")
    u.add_argument("--name")
    u.add_argument("--changelog")
    u.add_argument("--keep-old", action="store_true", help="don't archive the previous version (keep_versions > 1 implies it)")
    u.add_argument("--apply", action="store_true", help="really upload (default: dry run)")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        print(status(a.layer))
    else:
        upload(a.layer, a.zip, a.version, a.file_id, a.name, a.changelog, a.keep_old, a.apply)


if __name__ == "__main__":
    main(sys.argv[1:])
