"""Script Extender bridge: run Lua / console commands in the running game and read results back.

Transport: se_inject.ps1 attaches to the game's SE console and writes keystrokes into its input buffer
(WriteConsoleInput; no window focus needed). Output is read from the newest "Extender Runtime" log,
which must be newer than the game process (log file names are UTC; we compare real mtimes).
Each eval is wrapped in unique BEGIN/END markers so concurrent log noise can't be mistaken for it.
"""
import contextlib
import glob
import json
import os
import subprocess
import threading
import time
import uuid

from . import platform, sources

INJECTOR = os.path.join(os.path.dirname(__file__), "ps", "se_inject.ps1")

_console_lock = threading.Lock()


@contextlib.contextmanager
def _console_guard(timeout=120.0):
    """One console conversation at a time - across threads AND processes (the MCP server and a CLI run
    typing into the same game console interleave their keystrokes and hang the injector)."""
    with _console_lock:
        os.makedirs(sources.CACHE, exist_ok=True)
        fh = open(os.path.join(sources.CACHE, "console.lock"), "a+")
        deadline = time.time() + timeout
        try:
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.time() > deadline:
                        raise RuntimeError("another bg3-data process has been using the game console for "
                                           f"{timeout:.0f}s; wait for it or stop it")
                    time.sleep(0.2)
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh, fcntl.LOCK_UN)
            except OSError:
                pass
            fh.close()


def _cfg():
    cfg = sources.load_config()
    se = cfg.get("se") or {}
    se["inject_script"] = platform.to_native(se["inject_script"]) if se.get("inject_script") else INJECTOR
    ld = platform.larian_dir(cfg)
    se["log_dir"] = platform.to_native(se["log_dir"]) if se.get("log_dir") else (os.path.join(ld, "Script Extender Logs") if ld else "")
    se.setdefault("enabled", True)
    return se


_PROC_CACHE = {"at": 0.0, "value": None}


def game_process(max_age=10.0):
    """(pid, name, start_epoch) of the running game, or None. Finding it spawns PowerShell (~1-2 s), and a
    running game's pid/start don't change, so a positive answer is reused for `max_age` seconds."""
    if _PROC_CACHE["value"] and time.time() - _PROC_CACHE["at"] < max_age:
        return _PROC_CACHE["value"]
    v = _game_process()
    _PROC_CACHE.update(at=time.time(), value=v)
    return v


def _game_process():
    ps = ("Get-Process bg3,bg3_dx11 -ErrorAction SilentlyContinue | Select-Object -First 1 | "
          "ForEach-Object { $_.Id.ToString() + '|' + $_.ProcessName + '|' + "
          "([DateTimeOffset]$_.StartTime).ToUnixTimeSeconds().ToString() }")
    try:
        out = platform.run_win(["powershell.exe", "-NoProfile", "-Command", ps], timeout=20).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not out or "|" not in out:
        return None
    pid, name, start = out.splitlines()[0].split("|")
    return int(pid), name, int(start)


def current_log(proc=None):
    """Newest Extender Runtime log, only if written during the current game run."""
    se = _cfg()
    logs = glob.glob(os.path.join(se["log_dir"], "Extender Runtime*.log"))
    if not logs:
        return None
    newest = max(logs, key=os.path.getmtime)
    proc = proc or game_process()
    if proc and os.path.getmtime(newest) < proc[2] - 5:
        return None  # stale: from a previous run
    return newest


def status():
    se = _cfg()
    proc = game_process()
    lines = []
    if not se["enabled"]:
        lines.append("SE bridge disabled in layers.json (se.enabled=false)")
    if not proc:
        return "\n".join(lines + ["game not running (no bg3/bg3_dx11 process)"])
    pid, name, start = proc
    lines.append(f"game running: {name} pid {pid}, started {sources.iso(start)}")
    log = current_log(proc)
    if not log:
        lines.append("no Extender Runtime log from this run yet (Script Extender not loaded, or logging disabled)")
    else:
        lines.append(f"log: {os.path.basename(log)} (last write {sources.iso(os.path.getmtime(log))}; file names are UTC)")
        tail = _read_from(log, max(0, os.path.getsize(log) - 20000))
        state = [l for l in tail.splitlines() if "OnGameStateChanged" in l or "SessionLoaded" in l]
        if state:
            lines.append("latest state: " + state[-1].strip()[:160])
    lines.append(f"console injector: {se['inject_script']}" + ("" if os.path.isfile(se["inject_script"]) else "  [MISSING]"))
    return "\n".join(lines)


def _read_from(path, offset):
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read().decode("utf-8", errors="replace")


def _lua_string(s):
    """Encode arbitrary text as a single-line Lua string literal."""
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 32 or o == 127:
            out.append(f"\\{o:03d}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _inject(lines, pid):
    se = _cfg()
    tmp = os.path.join(platform.windows_temp(), f"bg3data_se_{uuid.uuid4().hex[:8]}.txt")
    with open(tmp, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")
    try:
        r = platform.run_win(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                              platform.to_win(se["inject_script"]), "-LinesFile", platform.to_win(tmp), "-ProcId", str(pid)], timeout=60)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if "ok" not in r.stdout:
        _PROC_CACHE["value"] = None  # the game may have exited or restarted
        raise RuntimeError(f"console injection failed: {r.stdout.strip() or r.stderr.strip()}")


def _preflight():
    se = _cfg()
    if not se["enabled"]:
        raise RuntimeError("SE bridge disabled (layers.json se.enabled=false)")
    proc = game_process()
    if not proc:
        raise RuntimeError("game not running")
    log = current_log(proc)
    if not log:
        raise RuntimeError("no Script Extender runtime log from this game run - is SE loaded with logging on?")
    return proc, log


def eval_lua(code, context="server", timeout=15.0):
    """Run Lua in the game; return {'ok', 'result', 'output': [printed lines]}."""
    if context not in ("server", "client"):
        raise ValueError("context must be 'server' or 'client'")
    with _console_guard():
        (pid, _, _), log = _preflight()
        tag = uuid.uuid4().hex[:10]
        chunk = (
            f'local __t={_lua_string(tag)}; Ext.Utils.Print("[BG3SE:"..__t..":BEGIN]"); '
            # SE replaces load() (2nd arg must be an env table), and load itself can throw: pcall it so
            # the END marker is always printed and failures come back as errors, not timeouts
            f'local __lok,__f,__e=pcall(load,{_lua_string(code)}); local __ok,__r; '
            f'if not __lok then __ok,__r=false,__f elseif __f then __ok,__r=pcall(__f) else __ok,__r=false,__e end; '
            f'local __s; local __js,__jv=pcall(Ext.Json.Stringify,{{r=__r}},{{Beautify=false,StringifyInternalTypes=true,IterateUserdata=true,AvoidRecursion=true,LimitDepth=10,LimitArrayElements=200}}); '
            f'__s=__js and __jv or Ext.Json.Stringify({{r=tostring(__r)}}); '
            f'Ext.Utils.Print("[BG3SE:"..__t..":END]"..(__ok and "OK" or "ERR").."|"..__s)'
        )
        offset = os.path.getsize(log)
        _inject([context, chunk], pid)
        deadline = time.time() + timeout
        while time.time() < deadline:
            text = _read_from(log, offset)
            end_marker = f"[BG3SE:{tag}:END]"
            if end_marker in text:
                begin = text.find(f"[BG3SE:{tag}:BEGIN]")
                body = text[begin:text.find(end_marker)] if begin >= 0 else ""
                output = [l for l in body.splitlines()[1:] if l.strip()]
                tail = text[text.find(end_marker) + len(end_marker):].splitlines()[0]
                status_, _, payload = tail.partition("|")
                try:
                    result = json.loads(payload).get("r")
                except ValueError:
                    result = payload
                return {"ok": status_ == "OK", "result": result, "output": output}
            time.sleep(0.25)
        raise TimeoutError(f"no result within {timeout:.0f}s (game paused/minimised in a menu, or console not accepting input?)")


def command(line, wait=3.0, context="server"):
    """Send one raw console line (e.g. '!apofeature X') and return the log lines it produced."""
    if "\n" in line or "\r" in line:
        raise ValueError("one console line at a time")
    with _console_guard():
        (pid, _, _), log = _preflight()
        offset = os.path.getsize(log)
        _inject([context, line], pid)
        time.sleep(max(0.5, min(wait, 60)))
        return [l for l in _read_from(log, offset).splitlines() if l.strip()]


def log_tail(filter_text=None, lines=60):
    log = current_log()
    if not log:
        raise RuntimeError("no Extender Runtime log from the current game run")
    # unfiltered: the tail is what matters; filtered: search (almost) the whole run - eval traffic can push
    # early lines (bootstrap, load errors) far back in the file
    window = 64_000_000 if filter_text else 400_000
    text = _read_from(log, max(0, os.path.getsize(log) - window)).splitlines()
    if filter_text:
        text = [l for l in text if filter_text.lower() in l.lower()]
    return os.path.basename(log), text[-lines:]


def live_stats(name, fields):
    """Field values of stats entry NAME as the game loaded them."""
    code = (
        f"local s=Ext.Stats.Get({_lua_string(name)}); if not s then return nil end; local o={{}}; "
        f"for _,k in ipairs({{{','.join(_lua_string(f) for f in fields)}}}) do "
        f"local ok,v=pcall(function() return s[k] end); "
        f"if ok and v~=nil then if type(v)=='table' or type(v)=='userdata' then local j,jv=pcall(Ext.Json.Stringify,v,{{Beautify=false}}); o[k]=j and jv or tostring(v) else o[k]=tostring(v) end end end; return o"
    )
    return eval_lua(code, "server", timeout=20)


def live_stats_many(entries, timeout=60.0):
    """{name: [fields]} -> {name: {field: value} | None} in ONE game round-trip."""
    spec = "{" + ",".join(f"[{_lua_string(n)}]={{{','.join(_lua_string(f) for f in fs)}}}" for n, fs in entries.items()) + "}"
    code = (
        f"local spec={spec}; local out={{}}; for n,fs in pairs(spec) do local s=Ext.Stats.Get(n); "
        f"if s then local o={{}}; for _,k in ipairs(fs) do local ok,v=pcall(function() return s[k] end); "
        f"if ok and v~=nil then if type(v)=='table' or type(v)=='userdata' then local j,jv=pcall(Ext.Json.Stringify,v,{{Beautify=false}}); "
        f"o[k]=j and jv or tostring(v) else o[k]=tostring(v) end end end; out[n]=o else out[n]=false end end; return out"
    )
    r = eval_lua(code, "server", timeout=timeout)
    if not r["ok"]:
        raise RuntimeError(f"live stats query failed in game: {r['result']}")
    return {n: (v or None) for n, v in (r["result"] or {}).items()}


def normalize(value):
    """Canonical form so index text and SE's JSON/typed values compare equal when they mean the same."""
    import re as _re
    v = value
    if isinstance(v, str) and v[:1] in "[{":
        try:
            v = json.loads(v)
        except ValueError:
            pass
    if isinstance(v, list) and v and all(isinstance(x, dict) and "Requirement" in x for x in v):
        # Requirements: [{"Requirement":"Immobile","Not":true,"Param":-1}] -> "!Immobile"
        v = ";".join(("!" if x.get("Not") else "") + str(x["Requirement"])
                     + (f"({x['Param']})" if x.get("Param") not in (-1, None) else "") for x in v)
    if isinstance(v, dict):
        # conditional rolls: {"Default": A, "CastOffhand": B} -> "A;CastOffhand[B]" (the stats text form)
        rest = [f"{k}[{v[k]}]" for k in sorted(v) if k != "Default"]
        v = ";".join(([str(v["Default"])] if "Default" in v else []) + rest)
    if isinstance(v, list):
        return ";".join(sorted(normalize(x) for x in v if normalize(x)))
    s = str(v).strip()
    if _re.fullmatch(r"h[0-9a-z]{16,48};\d+", s):
        s = s.split(";")[0]  # loca handle: the game drops the ;version suffix
    try:
        f = float(s)
        return str(int(f)) if f == int(f) else f"{f:g}"
    except ValueError:
        pass
    parts = [p for p in _re.split(r"\s*;\s*", s) if p]
    if len(parts) > 1:
        return ";".join(sorted(p.replace(" ", "").lower() for p in parts))
    return s.replace(" ", "").lower()


def comparable(game_value):
    """False for values the game only exposes as parsed userdata (functors), which have no text form."""
    return not (isinstance(game_value, str) and game_value.startswith("Array<stats::"))


EMPTY = {"", "none", "0", "[]", "{}", "null"}


def same(index_value, game_value):
    """Compare an index value with the game's. A cleared/empty index value means 'engine default', so any
    default-looking game value (0, None, [], '') matches; otherwise compare normalized forms."""
    ni, ng = normalize(index_value or ""), normalize(game_value if game_value is not None else "")
    if ni in EMPTY:
        return ng in EMPTY or index_value == ""
    return ni == ng


# ------------------------------------------------------------------ hot loading (no restart)
# SE only reads safe RELATIVE paths through the game's VFS, so a layer's files are mirrored as loose files
# under <game>/Data/Public/BG3DataHot_<layer>/ and loaded from there. Nothing here survives a restart or
# gets written into saves unless the game is saved while hot-loaded entries are in use.
import re as _re2
import shutil as _sh

HOT_PREFIX = "BG3DataHot_"


def _hot_root(cfg, layer):
    return os.path.join(cfg["base"]["game_data"], "Public", HOT_PREFIX + _re2.sub(r"[^A-Za-z0-9_]", "_", layer))


def _layer_mod(cfg, layer):
    for m in cfg["mods"]:
        if m["name"] == layer:
            return m
    raise ValueError(f"unknown mod layer {layer!r}; hot loading works on mod layers, not base")


def hot_load_stats(layer, files=None):
    """Mirror a mod layer's Stats/Generated/Data/*.txt into the game's Data folder, LoadStatsFile each
    (in name order), then Sync every entry they define. Returns a summary dict."""
    cfg = sources.load_config()
    mod = _layer_mod(cfg, layer)
    srcs = sources.mod_files(cfg, mod, "stats")
    if files:
        want = {f.lower() for f in files}
        srcs = [f for f in srcs if os.path.basename(f).lower() in want]
    if not srcs:
        raise ValueError(f"no stats files found for layer {layer!r}" + (f" matching {files}" if files else ""))
    root = _hot_root(cfg, layer)
    if os.path.isdir(root):
        _sh.rmtree(root)
    rel = []
    for f in srcs:
        dst = os.path.join(root, "Stats", os.path.basename(f))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        _sh.copy2(f, dst)
        rel.append("Public/" + os.path.basename(root) + "/Stats/" + os.path.basename(f))
    lua_files = "{" + ",".join(_lua_string(r) for r in rel) + "}"
    code = (
        f"local files={lua_files}; local res={{files={{}}, synced=0, sync_errors={{}}}}; "
        "for _,p in ipairs(files) do local ok,e=pcall(Ext.Stats.LoadStatsFile,p,true); "
        "local txt=Ext.IO.LoadFile(p,'data') or ''; local n=0; "
        "for name in txt:gmatch('new entry \"(.-)\"') do n=n+1; "
        "local sok,se_=pcall(Ext.Stats.Sync,name,false); if sok then res.synced=res.synced+1 elseif #res.sync_errors<10 then res.sync_errors[#res.sync_errors+1]=name..': '..tostring(se_) end end; "
        "res.files[#res.files+1]={file=p, ok=ok, err=(not ok) and tostring(e) or nil, entries=n} end; return res"
    )
    r = eval_lua(code, "server", timeout=120)
    if not r["ok"]:
        raise RuntimeError(f"hot load failed in game: {r['result']}")
    created = sum(1 for l in r["output"] if "Create new entry" in l)
    errors = [l for l in r["output"] if "Unrecognized line" not in l
              and _re2.search(r"\b(error|failed|invalid|could not)\b", l, _re2.I)][:10]
    warnings = sum(1 for l in r["output"] if "Unrecognized line" in l)  # e.g. // comments: skipped harmlessly
    return {"layer": layer, "files": r["result"]["files"], "synced": r["result"]["synced"],
            "sync_errors": r["result"]["sync_errors"], "created_new": created, "engine_errors": errors,
            "skipped_lines": warnings, "hot_root": root}


def hot_load_loca(layer):
    """Push a mod layer's English loca into the running game (Ext.Loca.UpdateTranslatedString)."""
    from . import parse
    cfg = sources.load_config()
    mod = _layer_mod(cfg, layer)
    rows = []
    for f in sources.mod_files(cfg, mod, "loca"):
        for h, _, text in parse.parse_loca(f):
            if h and "\t" not in text:
                rows.append(h + "\t" + text.replace("\r", "").replace("\n", "<br>"))
    root = _hot_root(cfg, layer)
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "loca.tsv"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(rows) + "\n")
    relp = "Public/" + os.path.basename(root) + "/loca.tsv"
    code = (
        f"local t=Ext.IO.LoadFile({_lua_string(relp)},'data') or ''; local n,bad=0,0; "
        "for line in t:gmatch('[^\\n]+') do local h,s=line:match('^([^\\t]+)\\t(.*)$'); "
        "if h then local ok=pcall(Ext.Loca.UpdateTranslatedString,h,s); if ok then n=n+1 else bad=bad+1 end end end; "
        "return {updated=n, failed=bad}"
    )
    r = eval_lua(code, "server", timeout=60)
    if not r["ok"]:
        raise RuntimeError(f"loca hot load failed: {r['result']}")
    return {"layer": layer, "strings_sent": len(rows), **(r["result"] or {})}


def hot_clean():
    """Remove every loose hot-load folder from the game's Data/Public."""
    cfg = sources.load_config()
    pub = os.path.join(cfg["base"]["game_data"], "Public")
    removed = []
    for d in glob.glob(os.path.join(pub, HOT_PREFIX + "*")) + glob.glob(os.path.join(pub, "BG3DataHotProbe")):
        _sh.rmtree(d, ignore_errors=True)
        removed.append(os.path.basename(d))
    return removed
