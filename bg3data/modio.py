"""mod.io through its REST API (2026-10-07): everything around a Toolkit upload.

  python -m bg3data.modio status LAYER                      profile, live file, scan, tags, dependencies, comment count
  python -m bg3data.modio edit LAYER [--name N] [--summary S] [--description-file F] [--visible 0|1] [--apply]
  python -m bg3data.modio tags LAYER [--add T ...] [--remove T ...] [--apply]
  python -m bg3data.modio deps LAYER [--add MODID ...] [--remove MODID ...] [--apply]
  python -m bg3data.modio logo LAYER IMAGE [--apply]
  python -m bg3data.modio comments LAYER [--limit N]
  python -m bg3data.modio reply LAYER COMMENT_ID TEXT [--apply]
  python -m bg3data.modio golive LAYER [--apply]            visible = 1

Uploads stay with the Larian Toolkit (bg3data.toolkit_ui publish): BG3's mod.io settings (game 6715, submission_option 0)
take uploads only from the developer's tool. A Toolkit modfile is a zip holding the .pak, with metadata_blob
"hash=<pak MD5>;filesize=<bytes>;".

Facts (verified 2026-10-07): the API lives on the per-game host https://g-<game>.modapi.io/v1 (api.mod.io answers 404
"deprecated"); requests need a real User-Agent (Cloudflare 1010 otherwise); reads take ?api_key=, writes and /me a Bearer
token. Credentials: ~/.config/bg3-data-mcp/modio.env (MODIO_API_KEY read-only, MODIO_ACCESS_TOKEN OAuth read+write) or the
same names in the environment. A layer names its mod in layers.json: "modio": {"game": 6715, "mod": 5626318}.
Every write is a dry run unless apply=True (prints the request it would send).
"""
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

ENV_FILE = os.path.expanduser("~/.config/bg3-data-mcp/modio.env")
UA = "bg3-data-mcp/modio (+https://github.com/holypolarpanda7/bg3-data-mcp)"
GAME = 6715          # Baldur's Gate 3


def creds():
    c = {k: os.environ.get(k) for k in ("MODIO_API_KEY", "MODIO_ACCESS_TOKEN")}
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                k = k.strip()
                if k in c and not c[k]:
                    c[k] = v.strip().strip('"').strip("'")
    return c


def entry(layer):
    from . import testing
    mio = testing.mod_entry(layer).get("modio") or {}
    if not mio.get("mod"):
        raise SystemExit(f'layer {layer!r} has no "modio": {{"mod": ...}} in layers.json')
    return {"game": mio.get("game", GAME), **mio}


def _host(game):
    return f"https://g-{game}.modapi.io/v1"


def _form(data):
    pairs = []
    for k, v in data.items():
        if isinstance(v, (list, tuple)):
            pairs += [(f"{k}[]", str(x)) for x in v]
        elif v is not None:
            pairs.append((k, str(v)))
    return urllib.parse.urlencode(pairs).encode()


def _multipart(fields, files):
    b = uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out += [f"--{b}\r\n".encode(), f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()]
    for k, path in files.items():
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        out += [f"--{b}\r\n".encode(),
                f'Content-Disposition: form-data; name="{k}"; filename="{os.path.basename(path)}"\r\nContent-Type: {ctype}\r\n\r\n'.encode(),
                open(path, "rb").read(), b"\r\n"]
    out.append(f"--{b}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={b}"


def call(method, path, game=GAME, data=None, files=None, auth=None, query=None, platform="windows"):
    """One request. Reads use the API key unless auth=True; writes always use the token."""
    c = creds()
    write = method != "GET"
    use_token = write if auth is None else auth
    q = dict(query or {})
    h = {"User-Agent": UA, "Accept": "application/json"}
    if platform:   # filters files to ones live on that platform - None for an owner's view of pending files
        h["X-Modio-Platform"] = platform
    if use_token:
        if not c["MODIO_ACCESS_TOKEN"]:
            raise SystemExit(f"no MODIO_ACCESS_TOKEN in {ENV_FILE}")
        h["Authorization"] = "Bearer " + c["MODIO_ACCESS_TOKEN"]
    else:
        if not c["MODIO_API_KEY"]:
            raise SystemExit(f"no MODIO_API_KEY in {ENV_FILE}")
        q["api_key"] = c["MODIO_API_KEY"]
    url = _host(game) + path + ("?" + urllib.parse.urlencode(q) if q else "")
    body = None
    if files:
        body, ctype = _multipart(data or {}, files)
        h["Content-Type"] = ctype
    elif data is not None:
        body = _form(data)
        h["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path}: HTTP {e.code} - {e.read().decode(errors='replace')[:400]}") from None


def _mod(layer):
    e = entry(layer)
    return e, call("GET", f"/games/{e['game']}/mods/{e['mod']}", e["game"])


def status(layer):
    e, m = _mod(layer)
    g, mid = e["game"], e["mod"]
    mf = m.get("modfile") or {}
    deps = call("GET", f"/games/{g}/mods/{mid}/dependencies", g).get("data", [])
    com = call("GET", f"/games/{g}/mods/{mid}/comments", g, query={"_limit": 1})
    vis = {0: "hidden", 1: "public"}.get(m.get("visible"), m.get("visible"))
    st = {0: "not accepted", 1: "accepted", 3: "deleted"}.get(m.get("status"), m.get("status"))
    scan = {0: "not scanned", 1: "clean", 2: "infected", 3: "scan failed", 4: "file too large", 5: "file not found",
            6: "error"}.get(mf.get("virus_status"), mf.get("virus_status"))
    out = [f"{layer}: mod.io game {g} mod {mid} - {m.get('name')!r} (name_id {m.get('name_id')}), {vis}, {st}",
           f"  summary: {m.get('summary')}",
           f"  live file: {mf.get('id')} v{mf.get('version')} {mf.get('filename')} ({mf.get('filesize')} bytes), scan {scan}, "
           f"platforms {[(p.get('platform'), 'approved' if p.get('status') == 1 else p.get('status')) for p in mf.get('platforms') or []]}",
           f"  metadata_blob: {mf.get('metadata_blob')}",
           f"  tags: {[t.get('name') for t in m.get('tags') or []]}",
           f"  dependencies: {[(d.get('mod_id'), d.get('name')) for d in deps]}",
           f"  comments: {com.get('result_total', '?')}",
           f"  page: {m.get('profile_url')}"]
    return "\n".join(out)


def _write(layer, method, path, data=None, files=None, apply=False, what=""):
    e = entry(layer)
    shown = {k: (v if len(str(v)) < 200 else str(v)[:200] + "...") for k, v in (data or {}).items()}
    line = f"{'APPLY' if apply else 'DRY RUN'}: {method} /games/{e['game']}/mods/{e['mod']}{path} {json.dumps(shown)}" + \
           (f" files={list(files.values())}" if files else "") + (f" - {what}" if what else "")
    if not apply:
        return line
    r = call(method, f"/games/{e['game']}/mods/{e['mod']}{path}", e["game"], data=data, files=files)
    return line + "\n  -> " + json.dumps({k: r.get(k) for k in ("id", "name", "name_id", "visible", "summary", "message")
                                          if k in r} if isinstance(r, dict) else r)[:400]


def edit(layer, name=None, summary=None, description=None, visible=None, name_id=None, apply=False):
    data = {k: v for k, v in (("name", name), ("summary", summary), ("description", description), ("visible", visible),
                              ("name_id", name_id)) if v is not None}
    if not data:
        return "nothing to change"
    return _write(layer, "PUT", "", data, apply=apply, what="edit the mod profile")


def tags(layer, add=(), remove=(), apply=False):
    out = []
    if add:
        out.append(_write(layer, "POST", "/tags", {"tags": list(add)}, apply=apply, what="add tags"))
    if remove:
        out.append(_write(layer, "DELETE", "/tags", {"tags": list(remove)}, apply=apply, what="remove tags"))
    return "\n".join(out) or "nothing to change"


def deps(layer, add=(), remove=(), apply=False):
    out = []
    if add:
        out.append(_write(layer, "POST", "/dependencies", {"dependencies": list(add)}, apply=apply, what="add dependencies"))
    if remove:
        out.append(_write(layer, "DELETE", "/dependencies", {"dependencies": list(remove)}, apply=apply, what="remove dependencies"))
    return "\n".join(out) or "nothing to change"


def logo(layer, image, apply=False):
    if not os.path.isfile(image):
        raise SystemExit(f"no such file: {image}")
    return _write(layer, "POST", "/media", files={"logo": image}, apply=apply, what="replace the logo")


def comments(layer, limit=20):
    e = entry(layer)
    r = call("GET", f"/games/{e['game']}/mods/{e['mod']}/comments", e["game"], query={"_limit": limit, "_sort": "-date_added"})
    out = [f"{layer}: {r.get('result_total', 0)} comment(s), newest first"]
    for c in r.get("data", []):
        who = (c.get("user") or {}).get("username")
        reply = f" (reply to {c.get('reply_id')})" if c.get("reply_id") else ""
        out.append(f"  #{c.get('id')}{reply} {who}: {(c.get('content') or '').strip()[:300]}")
    return "\n".join(out)


def reply(layer, comment_id, text, apply=False):
    return _write(layer, "POST", "/comments", {"content": text, "reply_id": comment_id}, apply=apply, what="reply to a comment")


def golive(layer, apply=False):
    return _write(layer, "PUT", "", {"visible": 1}, apply=apply, what="make the mod public")


def files(layer, limit=5):
    """The newest files, pending ones included (a new Toolkit upload has no platform yet: approve it with `platforms`)."""
    e = entry(layer)
    r = call("GET", f"/games/{e['game']}/mods/{e['mod']}/files", e["game"], auth=True, platform=None,
             query={"_sort": "-date_added", "_limit": limit})
    out = [f"{layer}: newest {limit} file(s)"]
    for f in r.get("data", []):
        plats = [(p.get("platform"), {0: "pending", 1: "approved", 2: "denied"}.get(p.get("status"), p.get("status"))) for p in f.get("platforms") or []]
        out.append(f"  file {f['id']} v{f.get('version')} {f.get('filename')} scan {f.get('virus_status')} platforms {plats or 'none set'}")
    return "\n".join(out)


def platforms(layer, file_id, approve=(), deny=(), apply=False):
    """Set a file's platform status (BG3: windows unmoderated; mac / xboxseriesx / ps5 go to moderation)."""
    if not approve:
        return "nothing to change (name the platforms to approve; the list replaces the file's platforms)"
    # PUT the file with its platform list (verified 2026-10-07); POST /files/<id>/platforms answers 403 even for the owner
    return _write(layer, "PUT", f"/files/{file_id}", {"platforms": list(approve)}, apply=apply, what=f"approve file {file_id} for {list(approve)}")


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m bg3data.modio")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("files")
    p.add_argument("layer")
    p.add_argument("--limit", type=int, default=5)
    p = sub.add_parser("platforms")
    p.add_argument("layer")
    p.add_argument("file_id", type=int)
    p.add_argument("--approve", nargs="*", default=[])
    p.add_argument("--deny", nargs="*", default=[])
    p.add_argument("--apply", action="store_true")
    for n in ("status", "comments", "golive"):
        p = sub.add_parser(n)
        p.add_argument("layer")
        if n == "comments":
            p.add_argument("--limit", type=int, default=20)
        if n == "golive":
            p.add_argument("--apply", action="store_true")
    p = sub.add_parser("edit")
    p.add_argument("layer")
    p.add_argument("--name")
    p.add_argument("--name-id")
    p.add_argument("--summary")
    p.add_argument("--description-file")
    p.add_argument("--visible", type=int, choices=(0, 1))
    p.add_argument("--apply", action="store_true")
    for n in ("tags", "deps"):
        p = sub.add_parser(n)
        p.add_argument("layer")
        p.add_argument("--add", nargs="*", default=[])
        p.add_argument("--remove", nargs="*", default=[])
        p.add_argument("--apply", action="store_true")
    p = sub.add_parser("logo")
    p.add_argument("layer")
    p.add_argument("image")
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("reply")
    p.add_argument("layer")
    p.add_argument("comment_id", type=int)
    p.add_argument("text")
    p.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "files":
        print(files(a.layer, a.limit))
    elif a.cmd == "platforms":
        print(platforms(a.layer, a.file_id, a.approve, a.deny, a.apply))
    elif a.cmd == "status":
        print(status(a.layer))
    elif a.cmd == "comments":
        print(comments(a.layer, a.limit))
    elif a.cmd == "golive":
        print(golive(a.layer, a.apply))
    elif a.cmd == "edit":
        desc = open(a.description_file, encoding="utf-8").read() if a.description_file else None
        print(edit(a.layer, a.name, a.summary, desc, a.visible, a.name_id, a.apply))
    elif a.cmd == "tags":
        print(tags(a.layer, a.add, a.remove, a.apply))
    elif a.cmd == "deps":
        print(deps(a.layer, a.add, a.remove, a.apply))
    elif a.cmd == "logo":
        print(logo(a.layer, a.image, a.apply))
    else:
        print(reply(a.layer, a.comment_id, a.text, a.apply))


if __name__ == "__main__":
    main(sys.argv[1:])
