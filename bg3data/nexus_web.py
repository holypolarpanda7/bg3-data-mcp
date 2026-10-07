"""Nexus Mods page management through a browser (2026-10-07): what the v3 API can't do - the mod page itself.

The user signs in once in a dedicated Edge window (own profile under %LOCALAPPDATA%\\bg3-data-mcp\\edge-nexus, remote
debugging port 9223; the cookies persist); everything after that is driven through the Chrome DevTools Protocol. WSL2's NAT
networking can't reach Windows' localhost, so the CDP client is bg3data/win/cdp_bridge.py running under a Windows Python
venv (%LOCALAPPDATA%\\bg3-data-mcp\\winpy, websocket-client).

Edit pages (Nexus 2026 layout): https://www.nexusmods.com/games/<game>/mods/<id>/edit/<tab>, tab = general | media |
permissions | documents | files | requirements | articles. The forms are React (headless UI): values are written through the
native value setter + input/change events so React sees them, then Save is clicked and the page re-read to verify.
"""
import json
import os
import subprocess
import sys
import time

from . import platform, sources

PORT = 9223
WINPY = r"C:\Users\holyp\AppData\Local\bg3-data-mcp\winpy\Scripts\python.exe"
PROFILE = r"C:\Users\holyp\AppData\Local\bg3-data-mcp\edge-nexus"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
BRIDGE = os.path.join(os.path.dirname(__file__), "win", "cdp_bridge.py")


class Browser:
    """One long-lived bridge process; tab = the first nexusmods.com page (opened if there is none)."""

    def __init__(self):
        winpy = ("/mnt/" + WINPY[0].lower() + WINPY[2:].replace("\\", "/")) if platform.IS_WSL else WINPY
        self.p = subprocess.Popen([winpy, platform.to_win(BRIDGE), str(PORT)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1, **({"cwd": "/mnt/c"} if platform.IS_WSL else {}))
        self.tab = None

    def cmd(self, **c):
        self.p.stdin.write(json.dumps(c) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        if not line:
            raise RuntimeError("the CDP bridge exited - is the automation Edge window open? (bg3data.nexus_web open)")
        return json.loads(line)

    def ensure_tab(self):
        r = self.cmd(op="tabs")
        if "error" in r:
            raise RuntimeError(r["error"])
        tabs = [t for t in r["tabs"] if "nexusmods.com" in t["url"]]
        self.tab = tabs[0]["id"] if tabs else self.cmd(op="new", url="https://www.nexusmods.com/")["id"]
        return self.tab

    def js(self, expr, timeout_note=""):
        if not self.tab:
            self.ensure_tab()
        r = self.cmd(op="eval", tab=self.tab, expr=expr)
        if "error" in r:
            raise RuntimeError(f"page script failed{timeout_note}: {r['error']}")
        return r.get("value")

    def go(self, url, settle=4.0, timeout=45.0):
        """Navigate and wait for the NEW document: readyState alone is already 'complete' on the old page, which once let a
        diff read the previous page's unsaved state (2026-10-07). performance.timeOrigin changes with each document."""
        if not self.tab:
            self.ensure_tab()
        before = self.js("performance.timeOrigin")
        self.cmd(op="navigate", tab=self.tab, url=url)
        t = time.time()
        while time.time() - t < timeout:
            time.sleep(0.5)
            try:
                if self.js("performance.timeOrigin") != before and self.js("document.readyState") == "complete":
                    break
            except RuntimeError:
                pass   # the context is being replaced mid-navigation
        time.sleep(settle)

    def wait(self, selector, timeout=30.0):
        """Wait until `selector` matches (the edit pages render client-side after load)."""
        t = time.time()
        while time.time() - t < timeout:
            if self.js(f"!!document.querySelector({json.dumps(selector)})"):
                time.sleep(0.8)
                return True
            time.sleep(0.5)
        raise RuntimeError(f"{selector} never appeared - signed out, or the page layout changed? (screenshot: {self.shot('wait-failed')})")

    def shot(self, tag):
        d = os.path.join(sources.CACHE, "nexus_web")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"{time.strftime('%Y%m%d-%H%M%S')}-{tag}.png")
        r = self.cmd(op="screenshot", tab=self.tab, path=platform.to_win(p))
        return p if "path" in r else None

    def close(self):
        try:
            self.p.stdin.close()
            self.p.terminate()
        except Exception:
            pass


def open_browser(url="https://www.nexusmods.com/"):
    """Start the automation Edge window (own profile, debugging port) if it isn't up; the user signs in there once."""
    try:
        b = Browser()
        b.ensure_tab()
        b.close()
        return {"ok": True, "already": True}
    except Exception:
        pass
    platform.run_win(["powershell.exe" if platform.IS_WSL else "powershell", "-NoProfile", "-Command",
                      f"Start-Process -FilePath '{EDGE}' -ArgumentList '--remote-debugging-port={PORT}','--user-data-dir={PROFILE}',"
                      f"'--no-first-run','--no-default-browser-check','{url}'"], timeout=30)
    time.sleep(5)
    return {"ok": True, "started": True}


def edit_url(nx, tab):
    return f"https://www.nexusmods.com/games/{nx['game']}/mods/{nx['mod']}/edit/{tab}"


# ---------------------------------------------------------------- reading a mod's edit pages
_SET = """
(function setValue(el, v) {
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  el.dispatchEvent(new Event('blur', {bubbles: true}));
})"""

_READ_GENERAL = """(() => {
  const byName = n => document.getElementById(n);
  const lab = t => { const l = [...document.querySelectorAll('label')].find(x => x.innerText.trim().startsWith(t));
                     const i = l && (document.getElementById(l.htmlFor) || l.parentElement.querySelector('input')); return i ? i.value : null; };
  const tas = [...document.querySelectorAll('textarea')];
  const short = tas.find(t => t.offsetParent && /nxm-text-area/.test(t.className));
  const inst = window.sceditor ? (tas.map(t => sceditor.instance(t)).find(Boolean)) : null;
  const tags = [...document.querySelectorAll('[class*=tag] button, [data-tag]')].map(x => x.innerText.trim()).filter(Boolean);
  return {name: (byName('mod-name')||{}).value, version: (byName('mod-version')||{}).value, author: (byName('author-name')||{}).value,
          category: lab('Category'), language: lab('Language'), summary: short ? short.value : null,
          description: inst ? inst.val() : null};
})()"""

_READ_REQS = """(async () => {
  // the legacy lists sit in collapsed <details> sections whose rows only render once opened (2026-10-07)
  document.querySelectorAll('main details').forEach(d => { d.open = true; });
  await new Promise(r => setTimeout(r, 1500));
  const radios = [...document.querySelectorAll('[role=radio]')].slice(0, 2).map(r => r.getAttribute('aria-checked') === 'true');
  const method = radios[1] ? 'legacy' : 'file-to-file';
  const out = {method, nexus: [], external: [], files: []};
  const main = document.querySelector('main') || document.body;
  // headings and tables in document order; a table belongs to the nearest heading before it
  const marks = [...main.querySelectorAll('*')].filter(e => e.tagName === 'TABLE' ||
      (e.children.length === 0 && ['Nexus Mods', 'External resources', 'File-to-file requirements'].includes((e.innerText || '').trim())));
  let cur = null;
  for (const e of marks) {
    if (e.tagName !== 'TABLE') { cur = e.innerText.trim(); continue; }
    const rows = [...e.querySelectorAll('tbody tr')].map(tr => ({cells: [...tr.querySelectorAll('td')].map(td => td.innerText.trim()),
                                                                   a: tr.querySelector('a[href]')})).filter(r => r.cells.join('').trim());
    if (cur === 'File-to-file requirements') rows.forEach(r => out.files.push({file: r.cells[0], category: r.cells[1], version: r.cells[2], requires: r.cells[4] || ''}));
    else if (method === 'legacy' && cur === 'Nexus Mods') rows.forEach(r => out.nexus.push({name: r.cells[0], note: r.cells[1] || '', url: r.a ? r.a.href : null}));
    else if (method === 'legacy' && cur === 'External resources') rows.forEach(r => out.external.push({name: r.cells[0], url: r.a ? r.a.href : (r.cells[1] || null), note: r.cells[2] || ''}));
  }
  return out;
})()"""


def read_page(b, nx):
    b.go(edit_url(nx, "general"), settle=1)
    b.wait("#mod-name")
    b.wait("textarea.nxm-text-area")
    g = b.js(_READ_GENERAL)
    b.go(edit_url(nx, "requirements"), settle=1)
    b.wait("[role=radio]")
    g["requirements"] = b.js(_READ_REQS)
    return g


def _nexus_entry(layer):
    from . import nexus
    return nexus.nexus_entry(layer)


def _spec_paths(layer):
    from . import testing
    root = os.path.join(testing.mod_entry(layer)["path"], "nexus")
    return root, os.path.join(root, "page.toml"), os.path.join(root, "description.bbcode")


def _toml_str(v):
    return json.dumps(v, ensure_ascii=False)


def pull(layer):
    """Read the live page into <mod>/nexus/page.toml + description.bbcode (the spec push applies)."""
    nx = _nexus_entry(layer)
    b = Browser()
    try:
        b.ensure_tab()
        live = read_page(b, nx)
    finally:
        b.close()
    root, spec, desc = _spec_paths(layer)
    os.makedirs(root, exist_ok=True)
    req = live["requirements"]
    lines = ["# Nexus Mods page for this mod - applied by `python -m bg3data.nexus_web push <layer>` (dry run without --apply).",
             f"# Pulled from the live page {time.strftime('%Y-%m-%d %H:%M')}. The full description is description.bbcode (BBCode).", "",
             f"name = {_toml_str(live['name'])}", f"version = {_toml_str(live['version'])}", f"author = {_toml_str(live['author'])}",
             f"summary = {_toml_str(live['summary'])}", "", "[requirements]", f"method = {_toml_str(req['method'])}"]
    for kind in ("nexus", "external"):
        for r in req[kind]:
            lines += ["", f"[[requirements.{kind}]]", f"name = {_toml_str(r['name'])}"] + \
                     ([f"url = {_toml_str(r['url'])}"] if r.get("url") else []) + [f"note = {_toml_str(r['note'])}"]
    open(spec, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    open(desc, "w", encoding="utf-8", newline="\n").write(live["description"] or "")
    return {"spec": spec, "description": desc, "live": {k: (v if k != "description" else f"{len(v or '')} chars") for k, v in live.items()}}


def _load_spec(layer):
    import tomllib
    _, spec, desc = _spec_paths(layer)
    s = tomllib.load(open(spec, "rb"))
    s["description"] = open(desc, encoding="utf-8").read() if os.path.exists(desc) else None
    return s


def diff(layer, b=None):
    nx = _nexus_entry(layer)
    own = b is None
    b = b or Browser()
    try:
        b.ensure_tab()
        live = read_page(b, nx)
    finally:
        if own:
            b.close()
    spec = _load_spec(layer)
    changes = {k: (live.get(k), spec[k]) for k in ("name", "version", "author", "summary", "description")
               if spec.get(k) is not None and (spec[k] or "").strip() != (live.get(k) or "").strip()}
    lr, sr = live["requirements"], spec.get("requirements", {})
    req = {}
    if sr.get("method") and sr["method"] != lr["method"]:
        req["method"] = (lr["method"], sr["method"])
    for kind in ("nexus", "external"):
        have = {r["name"].strip().lower() for r in lr.get(kind, [])}
        want = [r for r in sr.get(kind, []) if r["name"].strip().lower() not in have]
        if want:
            req[f"add_{kind}"] = want
        names = {r["name"].strip().lower() for r in sr.get(kind, [])}
        extra = [r["name"] for r in lr.get(kind, []) if r["name"].strip().lower() not in names]
        if extra:
            req[f"remove_{kind}"] = extra
    return {"fields": changes, "requirements": req}


def _show(d):
    out = []
    for k, (old, new) in d["fields"].items():
        if k == "description":
            out.append(f"  description: {len(old or '')} -> {len(new or '')} chars")
        else:
            out.append(f"  {k}: {old!r} -> {new!r}")
    for k, v in d["requirements"].items():
        out.append(f"  requirements {k}: {v}")
    return "\n".join(out) or "  (live page matches the spec)"


def push(layer, apply=False):
    """Make the live page match <mod>/nexus/page.toml + description.bbcode. Dry run (prints the diff) unless apply=True;
    with apply: General fields -> Save, requirements -> add/remove + Save, then re-read and report what still differs."""
    nx = _nexus_entry(layer)
    b = Browser()
    try:
        b.ensure_tab()
        d = diff(layer, b)
        head = f"{layer}: Nexus mod {nx['mod']} - {'APPLY' if apply else 'DRY RUN'}\n" + _show(d)
        if not apply or (not d["fields"] and not d["requirements"]):
            return head
        spec = _load_spec(layer)
        log = []
        if d["fields"]:
            b.go(edit_url(nx, "general"), settle=1)
            b.wait("#mod-name")
            js = [f"const setValue = {_SET};"]
            for k, sel in (("name", "#mod-name"), ("version", "#mod-version"), ("author", "#author-name")):
                if k in d["fields"]:
                    js.append(f"setValue(document.querySelector('{sel}'), {json.dumps(spec[k])});")
            if "summary" in d["fields"]:
                js.append("setValue([...document.querySelectorAll('textarea')].find(t => t.offsetParent && /nxm-text-area/.test(t.className)), "
                          f"{json.dumps(spec['summary'])});")
            if "description" in d["fields"]:
                js.append("const tas = [...document.querySelectorAll('textarea')]; const inst = tas.map(t => sceditor.instance(t)).find(Boolean);"
                          f"inst.val({json.dumps(spec['description'])}); inst.updateOriginal();"
                          "const raw = tas.find(t => t.parentElement && /bbcode-editor/.test(t.parentElement.className)) || tas[tas.length - 1];"
                          f"setValue(raw, {json.dumps(spec['description'])}); inst.trigger && inst.trigger('valuechanged');")
            js.append("return true;")
            b.js("(() => {" + "\n".join(js) + "})()")
            time.sleep(1)
            log.append("general: " + str(_click_save(b)))
        if d["requirements"]:
            log.append("requirements: " + str(_apply_requirements(b, nx, d["requirements"])))
        b.shot(f"{layer}-after")
        after = diff(layer, b)
        return head + "\n" + "\n".join(log) + "\nafter save:\n" + _show(after)
    finally:
        b.close()


def _click_save(b):
    r = b.js("""(() => { const s = [...document.querySelectorAll('button')].find(x => x.innerText.trim() === 'Save' && x.getClientRects().length && !x.disabled);  // fixed bars have no offsetParent
                         if (!s) return 'no enabled Save button'; s.click(); return 'clicked'; })()""")
    time.sleep(4)
    return r


def _apply_requirements(b, nx, req):
    out = []
    b.go(edit_url(nx, "requirements"), settle=1)
    b.wait("[role=radio]")
    if "method" in req:
        idx = 1 if req["method"][1] == "legacy" else 0
        b.js(f"document.querySelectorAll('[role=radio]')[{idx}].click()")
        time.sleep(1.5)
        out.append(f"method -> {req['method'][1]}")
    for r in req.get("add_external", []):
        # the "Add requirement" button under the External resources heading (index-based picking clicked the wrong one)
        b.js("""(() => { const bs = [...document.querySelectorAll('button')].filter(x => /Add requirement/i.test(x.innerText || ''));
                       const e = bs.find(x => /External resources/.test(x.closest('div').parentElement.innerText)) || bs[bs.length - 1];
                       e.scrollIntoView(); e.click(); })()""")
        time.sleep(1.5)
        b.js(f"(() => {{ const setValue = {_SET}; const d = document.querySelector('[role=dialog]');"
             f"setValue(d.querySelector('input[name=name]'), {json.dumps(r['name'])});"
             f"setValue(d.querySelector('input[name=url]'), {json.dumps(r.get('url') or '')});"
             f"setValue(d.querySelector('input[name=notes]'), {json.dumps(r.get('note') or '')});"
             "[...d.querySelectorAll('button')].find(x => x.innerText.trim() === 'Save').click(); })()")
        time.sleep(2)
        out.append(f"added external {r['name']}")
    for kind in ("add_nexus", "remove_nexus", "remove_external"):
        if req.get(kind):
            out.append(f"{kind} {req[kind]}: not automated yet - do it on the page")
    out.append("save: " + str(_click_save(b)))
    return out


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m bg3data.nexus_web")
    ap.add_argument("cmd", choices=["open", "pull", "diff", "push"])
    ap.add_argument("layer", nargs="?")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "open":
        print(open_browser())
    elif a.cmd == "pull":
        print(json.dumps(pull(a.layer), indent=1, ensure_ascii=False))
    elif a.cmd == "diff":
        print(_show(diff(a.layer)))
    else:
        print(push(a.layer, a.apply))

if __name__ == "__main__":
    main(sys.argv[1:])
