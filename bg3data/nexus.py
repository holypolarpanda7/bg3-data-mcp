"""Nexus Mods publishing through the v3 API (open beta; the API behind the official Nexus-Mods/upload-action), 2026-10-07.

  python -m bg3data.nexus status LAYER                       the layer's Nexus mod and its files (ids, versions, categories)
  python -m bg3data.nexus upload LAYER ZIP --version V [--file-id ID] [--name N] [--changelog TEXT] [--keep-old] [--apply]
                                                              a new version of the layer's main file (dry run without --apply)

What the API can do: upload a file, add a version to an existing mod file (archiving the previous one), create a new file on an
existing mod, changelog entries, per-file-version requirements on other Nexus files. What it can't: create a mod page or edit
its title, description, images or page requirements - those stay on the website.

Config: the API key from NEXUS_API_KEY or ~/.config/bg3-data-mcp/nexus.env (NEXUS_API_KEY=...; create one at
https://www.nexusmods.com/settings/api-keys). A layer names its page in layers.json:
  "nexus": {"game": "baldursgate3", "mod": 20246, "file": "<v3 mod-file id of the main file, from `status`>"}
Every call needs the key, reads included.
"""
import json
import os
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


def status(layer):
    nx, mod, files = mod_info(layer)
    out = [f"{layer}: Nexus {nx['game']} mod {nx['mod']} = v3 mod {mod['id']} - {mod.get('name')!r}"]
    for f in files:
        v = f.get("latest_version") or f.get("version") or {}
        out.append(f"  file {f.get('id')}  {f.get('name')!r}  category {f.get('category') or v.get('category')}  "
                   f"version {v.get('version')}  uploaded {v.get('uploaded_at')}")
    if not nx.get("file"):
        out.append('  -> set "file" (the main file\'s id above) under "nexus" in layers.json before uploading')
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
    body = {"name": name or target.get("name") or os.path.basename(zip_path), "version": version, "file_category": "main",
            "archive_existing_file": not keep_old, "update_mod_version": True, "primary_mod_manager_download": True}
    log(f"{'UPLOAD' if apply else 'DRY RUN'}: {os.path.basename(zip_path)} ({os.path.getsize(zip_path)} bytes) -> mod {nx['mod']} "
        f"({mod.get('name')!r}) file {fid} ({target.get('name')!r}) as version {version}"
        + ("" if keep_old else "; the previous version is archived"))
    log(f"  version request: {json.dumps(body)}")
    if changelog:
        log(f"  changelog {version}: {changelog[:200]}")
    if not apply:
        return None
    uid = _upload(zip_path, log)
    ver = call("POST", f"/mod-files/{fid}/versions", {**body, "upload_id": uid})["data"]
    log(f"  created version {ver.get('version', {}).get('id') if isinstance(ver.get('version'), dict) else ver}")
    if changelog:
        call("POST", f"/mods/{mod['id']}/changelogs", {"version": version, "changelog": changelog})
        log("  changelog added")
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
    u.add_argument("--keep-old", action="store_true", help="don't archive the previous version")
    u.add_argument("--apply", action="store_true", help="really upload (default: dry run)")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        print(status(a.layer))
    else:
        upload(a.layer, a.zip, a.version, a.file_id, a.name, a.changelog, a.keep_old, a.apply)


if __name__ == "__main__":
    main(sys.argv[1:])
