"""Local release gate for a mod layer (2026-10-06): everything a release must pass, run on the modder's own machine
(the base game paks, dependency paks and the game itself can't go to a hosted CI runner).

  python -m bg3data.gate LAYER [--builds affected|all|ID,ID] [--seed SHA --from-log] [--no-ingame] [--post]

Steps, stopping at the first that fails:
  clean    the mod's git tree has no changes (the result belongs to a commit)
  static   every .lsx/.xml under Mods/ and Public/ parses; no XML comments in Localization (they crash the Toolkit)
  regen    the layer's `regen` commands (layers.json) change nothing (generated files are current)
  lint     bg3_lint_stats / bg3_lint_progressions / bg3_lint_rules / bg3_lint_container_tests are clean
  ingame   deploy + restart, then the builds the commit needs (see below), each stopping at its first failure
Result: <cache>/gate/<layer>.json (per-build pass records), a report <cache>/gate/<layer>-<sha>.md, and with --post a GitHub
commit status "bg3data/gate" on the commit (posted later with `post` if the commit wasn't pushed yet).

Which builds a commit needs: a build has a record of the last commit it passed at. It is owed a run when it never passed, or
when the changes between that commit and this one reach it - stats entries, list and progression nodes and test cases it
uses (its footprint: everything reachable from its class and subclass progressions and its cases). Script Extender code,
meta.lsx, race/other progression nodes and files the matcher can't place make every build owed.
"""
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
import tomllib
import xml.etree.ElementTree as ET

from . import sources

GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
LIST_FILES = ("SpellLists", "PassiveLists", "SkillLists", "AbilityLists", "EquipmentLists")


def _git(path, *args, check=True):
    r = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def _show(path, sha, f):
    r = subprocess.run(["git", "show", f"{sha}:{f}"], cwd=path, capture_output=True)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


# ---------------------------------------------------------------- state

def _state_path(layer):
    d = os.path.join(sources.CACHE, "gate")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{layer}.json")


def load_state(layer):
    p = _state_path(layer)
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    return {"builds": {}, "runs": {}}


def save_state(layer, st):
    p = _state_path(layer)
    json.dump(st, open(p + ".tmp", "w", encoding="utf-8"), indent=1)
    os.replace(p + ".tmp", p)


# ---------------------------------------------------------------- build outcome

FAIL_RE = re.compile(r"\bFAIL|LEVEL-UP FAILED|\bERROR\b|NOT RUN|still in combat|NOT saved")


def build_passed(report, top=None):
    """A run_build report passed: it got to the end ("done in"), took its top level (`top`, when given) and has no failure
    line (KNOWN fails are allowed; WARN lines don't stop a build either)."""
    lines = [l.strip() for l in report.splitlines()]
    if not any(l.startswith("done in") for l in lines):
        return False
    if top and not any(l.startswith(f"L{top}: ok") for l in lines):
        return False
    for l in lines:
        if l.startswith("KNOWN"):
            continue
        if re.match(r"L\d+: ok", l):   # its "LEVEL CHECK: n FAIL" count includes KNOWN fails; the detail lines decide
            l = re.sub(r"LEVEL CHECK: [^|]*", "", l)
        if FAIL_RE.search(l):
            return False
        m = re.search(r"final cases: (\d+)/(\d+) passed", l)
        if m and m.group(1) != m.group(2):
            return False
    return True


def builds_from_log(log_path, layer, since=None):
    """{build id: passed} from a runbuilds log (bg3_test_build background): the LAST complete run of each build, only from
    batches started at or after `since` ("YYYY-MM-DD HH:MM:SS")."""
    from . import testing
    tops = {b["id"]: b["levels"][1] for b in testing.load_builds(layer)}
    text = open(log_path, encoding="utf-8", errors="replace").read().replace("\0", "")
    out, cur, buf, on = {}, None, [], since is None
    for line in text.splitlines():
        m = re.match(r"started (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", line)
        if m:
            on = on or m.group(1) >= since
            cur = None
            continue
        m = re.match(r"build ([\w-]+): level", line)
        if m:
            cur, buf = m.group(1), []
            continue
        m = re.match(r"=== ([\w-]+) finished", line)
        if m:
            if on and cur == m.group(1):
                out[cur] = build_passed("\n".join(buf), tops.get(cur))
            cur = None
            continue
        if cur:
            buf.append(line)
    return out


# ---------------------------------------------------------------- footprints

class Footprints:
    """What each build reaches in the resolved data: stats names, list and table UUIDs, class names, test case ids."""

    def __init__(self, store, active, layer):
        from . import testing
        self.s, self.active, self.layer = store, active, layer
        w, p = store._where(active)
        self.names = {n for (n,) in store.db.execute(f"SELECT DISTINCT name FROM stats WHERE {w}", p)}
        self.lists = {}
        for u, at in store.db.execute(f"SELECT uuid, attrs FROM lists WHERE {w} ORDER BY rank", p):
            self.lists[u] = json.loads(at)
        self.merged = {}
        for u, at in self.lists.items():
            if at.get("MergedInto"):
                self.merged.setdefault(at["MergedInto"], []).append(u)
        self.cds = {}
        for u, n, at in store.db.execute(f"SELECT uuid, name, attrs FROM staticdata WHERE kind='ClassDescription' AND {w} ORDER BY rank", p):
            self.cds[n] = (u, json.loads(at))
        self.prog = {}
        for t, at in store.db.execute(f"SELECT table_uuid, attrs FROM prog WHERE {w} ORDER BY rank", p):
            self.prog.setdefault(t, []).append(json.loads(at))
        self.class_tables = {at.get("ProgressionTableUUID") for _, at in self.cds.values()}
        self.builds = {b["id"]: b for b in testing.load_builds(layer)}
        self.cases = testing.load_cases(layer)
        self.assigned = testing.assign_cases(self.cases, list(self.builds.values()))
        for bid, b in self.builds.items():
            for pat in b.get("final_cases") or []:
                self.assigned[bid] += [c for c in self.cases if fnmatch.fnmatch(c["id"], pat)]
        self._tok = {}
        self._fp = {}

    def _stat_tokens(self, name):
        if name not in self._tok:
            w, p = self.s._where(self.active)
            toks = set()
            for u, d in self.s.db.execute(f"SELECT using_, data FROM stats WHERE name=? AND {w}", [name] + p):
                txt = f"{u or ''} {d or ''}"
                toks |= set(IDENT.findall(txt)) | set(GUID.findall(txt))
            self._tok[name] = toks
        return self._tok[name]

    def _expand(self, seeds):
        seen, todo = set(), list(seeds)
        while todo:
            x = todo.pop()
            if x in seen:
                continue
            seen.add(x)
            if x in self.lists:
                at = self.lists[x]
                txt = " ".join(str(v) for v in at.values())
                todo += [t for t in IDENT.findall(txt) if t in self.names]
                todo += self.merged.get(x, [])
            elif x in self.names:
                todo += [t for t in self._stat_tokens(x) if (t in self.names or t in self.lists) and t not in seen]
        return seen

    def footprint(self, bid):
        if bid in self._fp:
            return self._fp[bid]
        b = self.builds[bid]
        keys = {b["class"]}
        tables = set()
        for n in (b.get("class"), b.get("subclass")):
            if n and n in self.cds:
                u, at = self.cds[n]
                keys |= {n, u}
                if at.get("ProgressionTableUUID"):
                    tables.add(at["ProgressionTableUUID"])
        seeds = set()
        for t in tables:
            for at in self.prog.get(t, []):
                txt = " ".join(str(v) for v in at.values())
                seeds |= {x for x in IDENT.findall(txt) if x in self.names}
                seeds |= {x for x in GUID.findall(txt) if x in self.lists}
        cases = self.assigned.get(bid, [])
        for c in cases:
            txt = json.dumps(c)
            seeds |= {x for x in IDENT.findall(txt) if x in self.names}
        fp = {"refs": self._expand(seeds) | keys | tables, "cases": {c["id"] for c in cases}}
        self._fp[bid] = fp
        return fp


# ---------------------------------------------------------------- change detection

def _stats_entries(text):
    out, cur, buf = {}, None, []
    for line in (text or "").splitlines():
        m = re.match(r'\s*new entry "([^"]+)"', line)
        if m:
            if cur:
                out[cur] = "\n".join(buf)
            cur, buf = m.group(1), []
        elif cur and line.strip() and not line.strip().startswith("//"):
            buf.append(" ".join(line.split()))
    if cur:
        out[cur] = "\n".join(buf)
    return out


def _lsx_nodes(text):
    """{key: (signature, {UUID, TableUUID, Name, ParentGuid, MergedInto})} for every node with a UUID attribute; None if unparsable."""
    if text is None:
        return {}
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError:
        return None
    out = {}
    for node in root.iter("node"):
        attrs = {a.get("id"): a.get("value") for a in node.findall("attribute")}
        key = attrs.get("UUID") or attrs.get("MapKey")
        if not key:
            continue
        sig = ET.tostring(node, encoding="unicode")
        sig = re.sub(r"\s+", " ", sig)
        ids = {k: attrs[k] for k in ("UUID", "MapKey", "TableUUID", "Name", "ParentGuid", "MergedInto") if attrs.get(k)}
        out[f"{node.get('id')}:{key}"] = (sig, ids)
    return out


def _toml_items(text):
    try:
        doc = tomllib.loads(text) if text else {}
    except tomllib.TOMLDecodeError:
        return None
    suite = doc.get("suite", {})
    return ({b["id"]: json.dumps(b, sort_keys=True) for b in doc.get("build", []) if "id" in b},
            {c["id"]: json.dumps({**suite, **c}, sort_keys=True) for c in doc.get("case", []) if "id" in c})


def _strip_version(text):
    """meta.lsx with the ModuleInfo Version64 blanked (the release stamp)."""
    if text is None:
        return None
    head, sep, rest = text.partition('<node id="ModuleInfo">')
    return head + sep + re.sub(r'(<attribute id="Version64" type="int64" value=")\d+', r"\g<1>", rest, count=1)


def changes(path, a, b="HEAD"):
    """What changed between commits a and b in a mod repo: {"all": [reasons], "stats": set, "nodes": [ids dict],
    "progression_nodes": [ids dict], "builds": set, "cases": set, "ignored": [files]}."""
    ch = {"all": [], "stats": set(), "nodes": [], "progression_nodes": [], "builds": set(), "cases": set(), "ignored": []}
    files = [f for f in _git(path, "diff", "--name-only", a, b).splitlines() if f]
    for f in files:
        parts = f.split("/")
        base = os.path.basename(f)
        low = f.lower()
        if parts[0] == "Mods" and len(parts) > 2 and parts[2] == "Localization":
            ch["ignored"].append(f)
        elif base == "meta.lsx" and _strip_version(_show(path, a, f)) == _strip_version(_show(path, b, f)):
            ch["ignored"].append(f"{f} (version stamp)")    # a release's Version64 bump owes no builds
        elif parts[0] == "Mods" and (len(parts) > 2 and parts[2] == "ScriptExtender" or base == "meta.lsx"):
            ch["all"].append(f"{f} (script/meta)")
        elif parts[0] == "Public" and low.endswith(".txt") and "/stats/" in low:
            old, new = _stats_entries(_show(path, a, f)), _stats_entries(_show(path, b, f))
            ch["stats"] |= {n for n in old.keys() | new.keys() if old.get(n) != new.get(n)}
        elif parts[0] in ("Public", "Mods") and low.endswith(".lsx") and "/gui/" not in low:
            old, new = _lsx_nodes(_show(path, a, f)), _lsx_nodes(_show(path, b, f))
            if old is None or new is None:
                ch["all"].append(f"{f} (unparsable)")
                continue
            diff = [(new.get(k) or old.get(k))[1] for k in old.keys() | new.keys() if (old.get(k) or (None,))[0] != (new.get(k) or (None,))[0]]
            if base.startswith("Progressions"):
                ch["progression_nodes"] += diff
            elif base.startswith(LIST_FILES) or base.startswith("ClassDescriptions"):
                ch["nodes"] += diff
            elif diff:
                ch["all"].append(f"{f} ({len(diff)} node(s) the matcher can't place)")
        elif parts[0] == "tests" and low.endswith(".toml") and "/rules/" not in low:
            old, new = _toml_items(_show(path, a, f)), _toml_items(_show(path, b, f))
            if old is None or new is None:
                ch["all"].append(f"{f} (unparsable)")
                continue
            for i, kind in ((0, "builds"), (1, "cases")):
                o, n = old[i], new[i]
                ch[kind] |= {k for k in o.keys() | n.keys() if o.get(k) != n.get(k)}
        elif parts[0] in ("Mods", "Public") and not (low.endswith((".dds", ".png")) or "/gui/" in low or "/assets/" in low):
            ch["all"].append(f"{f} (unclassified mod file)")
        else:
            ch["ignored"].append(f)
    return ch


def _reached(names, refs):
    """names in refs, counting a numbered variant (Interrupt_X_3, an upcast) as reached when its base is."""
    return {n for n in names if n in refs or re.sub(r"_\d+$", "", n) in refs}


def owed(fps, ch, bid):
    """Why build bid must run for changes ch ([] = it needn't)."""
    if ch["all"]:
        return ["every build: " + ch["all"][0] + (f" (+{len(ch['all']) - 1} more)" if len(ch["all"]) > 1 else "")]
    fp = fps.footprint(bid)
    why = []
    hit = sorted(_reached(ch["stats"], fp["refs"]))
    if hit:
        why.append("stats " + ", ".join(hit[:4]) + ("..." if len(hit) > 4 else ""))
    for ids in ch["nodes"] + ch["progression_nodes"]:
        if set(ids.values()) & fp["refs"]:
            why.append(f"node {ids.get('Name') or ids.get('UUID')}")
            break
    if bid in ch["builds"]:
        why.append("build definition")
    hit = sorted(ch["cases"] & fp["cases"])
    if hit:
        why.append("cases " + ", ".join(hit[:3]) + ("..." if len(hit) > 3 else ""))
    return why


def unplaced(fps, ch, bids):
    """Progression nodes no build reaches (race/background tables...): they reach every character, so every build runs."""
    refs = set()
    for b in bids:
        refs |= fps.footprint(b)["refs"]
    out = [ids for ids in ch["progression_nodes"] if not set(ids.values()) & refs]
    return out


# ---------------------------------------------------------------- steps

def step_static(path):
    bad = []
    for f in _git(path, "ls-files", "Mods", "Public").splitlines():
        low = f.lower()
        if not low.endswith((".lsx", ".xml")):
            continue
        p = os.path.join(path, f)
        if not os.path.exists(p):
            continue
        try:
            ET.parse(p)
        except ET.ParseError as e:
            bad.append(f"{f}: {e}")
        if "/localization/" in low and "<!--" in open(p, encoding="utf-8", errors="replace").read():
            bad.append(f"{f}: XML comment in a localization file (crashes the Larian Toolkit)")
    return bad


def step_regen(m):
    from . import deploy
    lines = deploy.regen(m)
    return [l for l in lines if l.startswith("!!") or l.startswith("     ")]


def step_lint(store, active, layer):
    from . import lint, rulescheck, testing
    out = []
    for name, fn in (("stats", lambda: lint.lint_stats(store, active, layer)),
                     ("progressions", lambda: testing.lint_progressions(store, active, layer)),
                     ("rules", lambda: rulescheck.lint_rules(store, active, layer)),
                     ("container tests", lambda: testing.lint_container_tests(store, active, layer))):
        r = fn()
        head = r.splitlines()[0] if r else ""
        if not re.search(r" - clean\b|: 0 finding\(s\)|: clean$", head):
            out.append(f"{name}: " + "\n    ".join(r.splitlines()[:15]))
    return out


# ---------------------------------------------------------------- plan + run

def plan(store, active, layer, path, head, st, which="affected"):
    """[(build id, [reasons])] the commit needs, plus notes."""
    fps = Footprints(store, active, layer)
    runnable = [b for b, d in fps.builds.items() if d.get("from")]  # base builds only make start saves
    notes = []
    if which == "all":
        return [(b, ["all requested"]) for b in runnable], notes
    if which != "affected":
        ids = [x.strip() for x in which.split(",") if x.strip()]
        return [(b, ["requested"]) for b in ids], notes
    todo, cache = [], {}
    for b in runnable:
        rec = st["builds"].get(b)
        if not rec or not rec.get("pass_sha"):
            todo.append((b, ["never passed"]))
            continue
        sha = rec["pass_sha"]
        if sha == head:
            continue
        if sha not in cache:
            try:
                cache[sha] = changes(path, sha, head)
                up = unplaced(fps, cache[sha], runnable)
                if up:
                    cache[sha]["all"].append(f"progression node {up[0].get('Name') or up[0].get('UUID')} that no build reaches (race/background?)")
            except RuntimeError as e:
                cache[sha] = {"all": [f"can't diff from {sha[:8]}: {e}"]}
        why = owed(fps, cache[sha], b) if "stats" in cache[sha] else cache[sha]["all"]
        if why:
            todo.append((b, why))
    for sha, ch in cache.items():
        if "stats" in ch:
            reached = set()
            for b in runnable:
                reached |= fps.footprint(b)["refs"]
            lone = sorted(ch["stats"] - _reached(ch["stats"], reached))
            if lone:
                notes.append(f"changed since {sha[:8]} but reached by no build: " + ", ".join(lone[:10]) + ("..." if len(lone) > 10 else ""))
    return todo, notes


def post_status(path, sha, state, desc):
    url = _git(path, "remote", "get-url", "origin", check=False).strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", url)
    if not m:
        return f"not posted: origin {url!r} isn't a GitHub repo"
    r = subprocess.run(["gh", "api", f"repos/{m.group(1)}/statuses/{sha}", "-f", f"state={state}", "-f", "context=bg3data/gate",
                        "-f", f"description={desc[:140]}"], capture_output=True, text=True)
    return "posted" if r.returncode == 0 else f"not posted ({r.stderr.strip()[:120]}) - push the commit, then run `post`"


def run(layer, which="affected", ingame=True, post=False, log=print):
    from . import deploy, server, testing
    cfg, m, info, _ = deploy.mod_info(layer)
    path = m["path"]
    head = _git(path, "rev-parse", "HEAD").strip()
    st = load_state(layer)
    rep = [f"# Gate {layer} @ {head[:10]} ({time.strftime('%Y-%m-%d %H:%M %Z')})", ""]
    result = {"sha": head, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": {}}

    def finish(ok, desc):
        result["ok"], result["desc"] = ok, desc
        result["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if post:
            result["posted"] = post_status(path, head, "success" if ok else "failure", desc)
            log(f"status: {result['posted']}")
        st["runs"][head] = result
        save_state(layer, st)
        rep.append(f"\n**{'GREEN' if ok else 'RED'}**: {desc}")
        rp = os.path.join(os.path.dirname(_state_path(layer)), f"{layer}-{head[:10]}.md")
        open(rp, "w", encoding="utf-8").write("\n".join(rep) + "\n")
        log(f"{'GREEN' if ok else 'RED'}: {desc}\nreport: {rp}")
        return ok

    def step(name, problems):
        result["steps"][name] = problems
        rep.append(f"## {name}: " + ("ok" if not problems else f"{len(problems)} problem(s)"))
        rep.extend(f"- {p}" for p in problems)
        log(f"{name}: " + ("ok" if not problems else "\n  ".join([f"{len(problems)} problem(s)"] + problems[:20])))
        return not problems

    dirty = [l for l in _git(path, "status", "--porcelain").splitlines() if l.strip()]
    if dirty:
        real = [l for l in dirty if l.startswith("??") or
                subprocess.run(["git", "diff", "HEAD", "--ignore-cr-at-eol", "--quiet", "--", l[3:].strip('"')], cwd=path).returncode != 0]
        if not step("clean", [f"uncommitted: {l}" for l in real[:20]]):
            return finish(False, "uncommitted changes")
    if not step("static", step_static(path)):
        return finish(False, "static checks failed")
    if not step("regen", step_regen(m)):
        return finish(False, "generated files were stale (regen changed them - review and commit)")
    from . import index
    index.refresh(force=[layer], log=lambda *_: None)
    s, active = server._testing_store(None)
    if not step("lint", step_lint(s, active, layer)):
        return finish(False, "lint findings")
    todo, notes = plan(s, active, layer, path, head, st, which)
    rep.append(f"## plan: {len(todo)} build(s)")
    rep.extend(f"- {b}: {'; '.join(w)}" for b, w in todo)
    rep.extend(f"- note: {n}" for n in notes)
    log(f"plan: {len(todo)} build(s)" + "".join(f"\n  {b}: {'; '.join(w)}" for b, w in todo[:40]) + "".join(f"\n  note: {n}" for n in notes))
    if not ingame:
        owing = len(todo)
        return finish(False if owing else True, f"static/regen/lint clean; {owing} build(s) not run (--no-ingame)" if owing else "clean, no builds owed")
    if todo:
        r = testing.restart(layer, True)
        log(r)
        if "session loaded" not in r:
            step("ingame", [f"restart/deploy failed: {r.splitlines()[-1] if r else ''}"])
            return finish(False, "couldn't deploy and start the game")
    fps_top = {d["id"]: d["levels"][1] for d in testing.load_builds(layer)}
    failed = []
    for b, why in todo:
        t = time.time()
        log(f"build {b} ({'; '.join(why)})")
        try:
            r = testing.run_build(s, active, layer, b)
        except Exception as e:
            r = f"ERROR {type(e).__name__}: {e}"
        ok = build_passed(r, fps_top.get(b))
        rec = st["builds"].setdefault(b, {})
        rec.update({"last_sha": head, "last_ok": ok, "last_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        if ok:
            rec["pass_sha"] = head
        else:
            failed.append(b)
            rec["last_fail"] = [l.strip() for l in r.splitlines() if FAIL_RE.search(l)][:6]
        save_state(layer, st)
        log(f"=== {b} {'PASS' if ok else 'FAIL'} in {time.time() - t:.0f}s" + ("".join(f"\n    {x}" for x in rec.get("last_fail", [])) if not ok else ""))
        rep.append(f"- {b}: {'PASS' if ok else 'FAIL'}" + ("".join(f"\n    - {x}" for x in rec.get("last_fail", [])) if not ok else ""))
    step("ingame", [f"{b}: {', '.join(st['builds'][b].get('last_fail', [])[:2])}" for b in failed])
    if failed:
        return finish(False, f"{len(failed)}/{len(todo)} build(s) failed: {', '.join(failed[:5])}")
    return finish(True, f"clean; {len(todo)} build(s) run and passed")


def seed(layer, sha, log_path, since=None):
    """Record pass results from a runbuilds log as having run at commit sha (e.g. a batch run before the gate existed)."""
    st = load_state(layer)
    res = builds_from_log(log_path, layer, since)
    for b, ok in res.items():
        rec = st["builds"].setdefault(b, {})
        rec.update({"last_sha": sha, "last_ok": ok, "last_at": f"seeded from {os.path.basename(log_path)}"})
        if ok:
            rec["pass_sha"] = sha
    save_state(layer, st)
    return f"seeded {len(res)} build(s) at {sha[:10]}: {sum(res.values())} passed, failed: {', '.join(b for b, ok in res.items() if not ok) or 'none'}"


def status(layer):
    from . import deploy, testing
    _, m, _, _ = deploy.mod_info(layer)
    head = _git(m["path"], "rev-parse", "HEAD").strip()
    st = load_state(layer)
    runs = st.get("runs", {})
    lines = [f"gate {layer}: HEAD {head[:10]}"]
    r = runs.get(head)
    lines.append(f"  HEAD: {'GREEN' if r and r.get('ok') else 'RED' if r else 'not gated'}" + (f" - {r['desc']} ({r.get('finished')})" if r else ""))
    builds = [b["id"] for b in testing.load_builds(layer) if b.get("from")]
    never = [b for b in builds if not (st["builds"].get(b) or {}).get("pass_sha")]
    red = [b for b in builds if (st["builds"].get(b) or {}).get("last_ok") is False]
    lines.append(f"  builds: {len(builds)}; passed at some commit: {len(builds) - len(never)}; last run failed: {len(red)}")
    lines += [f"    FAIL {b}: {', '.join((st['builds'][b].get('last_fail') or [])[:2])}" for b in red]
    if never:
        lines.append(f"    never passed: {', '.join(never[:30])}" + ("..." if len(never) > 30 else ""))
    return "\n".join(lines)


def post(layer, sha=None):
    from . import deploy
    _, m, _, _ = deploy.mod_info(layer)
    sha = sha or _git(m["path"], "rev-parse", "HEAD").strip()
    r = load_state(layer).get("runs", {}).get(sha)
    if not r:
        return f"no gate run for {sha[:10]}"
    return post_status(m["path"], sha, "success" if r["ok"] else "failure", r["desc"])


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="bg3data.gate")
    ap.add_argument("cmd", choices=["run", "plan", "status", "seed", "post"])
    ap.add_argument("layer")
    ap.add_argument("--builds", default="affected")
    ap.add_argument("--no-ingame", action="store_true")
    ap.add_argument("--post", action="store_true")
    ap.add_argument("--sha")
    ap.add_argument("--log", default=os.path.join(sources.CACHE, "test_builds.log"))
    ap.add_argument("--since")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    out = open(a.out, "a", encoding="utf-8") if a.out else None

    def log(msg):
        print(msg, flush=True)
        if out:
            out.write(msg + "\n")
            out.flush()
    if a.cmd == "run":
        if out:
            log(f"started {time.strftime('%Y-%m-%d %H:%M:%S')}: gate {a.layer}")
        ok = run(a.layer, a.builds, not a.no_ingame, a.post, log)
        sys.exit(0 if ok else 1)
    if a.cmd == "plan":
        from . import deploy, server
        _, m, _, _ = deploy.mod_info(a.layer)
        head = _git(m["path"], "rev-parse", "HEAD").strip()
        s, active = server._testing_store(None)
        todo, notes = plan(s, active, a.layer, m["path"], head, load_state(a.layer), a.builds)
        log(f"{len(todo)} build(s) owed at {head[:10]}:" + "".join(f"\n  {b}: {'; '.join(w)}" for b, w in todo) + "".join(f"\n  note: {n}" for n in notes))
    elif a.cmd == "status":
        log(status(a.layer))
    elif a.cmd == "seed":
        log(seed(a.layer, a.sha, a.log, a.since))
    elif a.cmd == "post":
        log(post(a.layer, a.sha))


if __name__ == "__main__":
    main(sys.argv[1:])
