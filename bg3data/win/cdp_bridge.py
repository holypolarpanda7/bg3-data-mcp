"""Chrome DevTools Protocol bridge, run with a WINDOWS Python (bg3data.nexus_web starts it; 2026-10-07).

WSL2 in NAT mode can't reach Windows' localhost, where Edge's --remote-debugging-port listens, so the CDP client runs on the
Windows side. One JSON command per stdin line, one JSON reply per stdout line:
  {"op": "tabs"}                                   -> {"tabs": [{"id", "url", "title"}]}
  {"op": "eval", "tab": ID, "expr": JS}            -> {"value": ...} (awaits promises; returnByValue)
  {"op": "navigate", "tab": ID, "url": URL}        -> {"ok": true}
  {"op": "screenshot", "tab": ID, "path": P}       -> {"path": P}
  {"op": "new", "url": URL}                        -> {"id": ...}
Needs websocket-client (a venv under %LOCALAPPDATA%\\bg3-data-mcp\\winpy).
"""
import base64
import json
import sys
import urllib.request

import websocket

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9223
BASE = f"http://127.0.0.1:{PORT}"
_socks, _ids = {}, {"n": 0}


def _http(path, method="GET"):
    req = urllib.request.Request(BASE + path, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def _ws(tab):
    s = _socks.get(tab)
    if s is None:
        t = next((t for t in _http("/json/list") if t["id"] == tab), None)
        if not t:
            raise RuntimeError(f"no tab {tab}")
        s = websocket.create_connection(t["webSocketDebuggerUrl"], timeout=60, suppress_origin=True)
        _socks[tab] = s
    return s


def _call(tab, method, params=None):
    s = _ws(tab)
    _ids["n"] += 1
    mid = _ids["n"]
    s.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        m = json.loads(s.recv())
        if m.get("id") == mid:
            if "error" in m:
                raise RuntimeError(m["error"].get("message"))
            return m.get("result", {})


def handle(c):
    op = c.get("op")
    if op == "tabs":
        return {"tabs": [{"id": t["id"], "url": t["url"], "title": t["title"]} for t in _http("/json/list") if t.get("type") == "page"]}
    if op == "new":
        t = _http("/json/new?" + c.get("url", "about:blank"), "PUT")
        return {"id": t["id"]}
    if op == "eval":
        r = _call(c["tab"], "Runtime.evaluate", {"expression": c["expr"], "awaitPromise": True, "returnByValue": True})
        if r.get("exceptionDetails"):
            return {"error": (r["exceptionDetails"].get("exception") or {}).get("description") or r["exceptionDetails"].get("text")}
        return {"value": (r.get("result") or {}).get("value")}
    if op == "navigate":
        _call(c["tab"], "Page.navigate", {"url": c["url"]})
        return {"ok": True}
    if op == "screenshot":
        r = _call(c["tab"], "Page.captureScreenshot", {"format": "png"})
        open(c["path"], "wb").write(base64.b64decode(r["data"]))
        return {"path": c["path"]}
    return {"error": f"unknown op {op}"}


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        out = handle(json.loads(line))
    except Exception as e:  # report and keep serving
        out = {"error": f"{type(e).__name__}: {e}"}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
