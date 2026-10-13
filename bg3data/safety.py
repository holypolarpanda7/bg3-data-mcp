"""Safe test sessions (2026-10-12): the harness must never be able to damage a real campaign.

Why: the user's own Honour Mode campaign (leader "Gregor", 280 mods) lives in the same Savegames folder as the harness's saves
(leader "Tavizard") and is the game's newest save, so -continueGame, the main menu's Load screen or a stray Enter all reach it. A
folder-level sandbox (a separate profile / junction swap) is NOT safe: Steam Cloud tracks `_SAVE_Public/Savegames/Story/**` and
`_PROFILE_Public/modsettings.lsx`, and a swapped folder would sync as deletions. So these are independent layers, each of which
stops the bad outcome on its own:

  1. vault        protect(): every non-harness save folder is copied to cache/vault (content-addressed, last few versions kept)
                  before any game launch, so whatever happens there is a restorable copy. restore_hint() says how.
  2. baseline     begin_session(): vault + a size/mtime baseline of the protected files. session_violations() compares against it:
                  any protected file changed/added/removed during a harness session is an alarm.
  3. watchdog     start_watchdog(): a DETACHED process (python -m bg3data.safety watch; it outlives the MCP server, a /mcp reconnect
                  and any script that started it - an in-process thread died with them, 2026-10-12) that, while the game runs, checks (a) the baseline every few seconds and (b) the loaded
                  host's name against the protected leaders (e.g. "Gregor"); either one kills the game at once - before an
                  autosave - and writes cache/vault/alerts.log.
  4. box gate     classify_mod_verification(): a Mod Verification box (the game's "mods changed" prompt before a load) that lists
                  MISSING or disabled mods belongs to a campaign made with other mods - never a harness save, which is kept in
                  step with the dev profile. The harness may press Start Game only on a box that has no such section.
  5. no blind     continue_guard() (testing.py) never -continueGame's into a non-harness newest save; load_save verifies its
                  selection before pressing and never clicks the Load button blindly (gameui.py).

A harness save is a folder whose name starts with `game.test_leader` + "-" (default "Tavizard-"); everything else is protected.
`game.protected_leaders` (layers.json) lists the names whose host must never be loaded; it defaults to the leaders of saves whose
folder name contains "HonourMode".
"""
import hashlib
import json
import os
import shutil
import threading
import time

from . import platform

KEEP_VERSIONS = 3
BIG = 64 * 1024 * 1024          # files over this are fingerprinted by size+mtime only
VAULT = os.path.join(platform.cache_dir(), "vault")
_state = {"watchdog": None}


def _paths(savegames=None, test_leader=None):
    if savegames is None or test_leader is None:
        from . import testing
        _, g = testing.game_cfg()
        savegames = savegames or g["savegames"]
        test_leader = test_leader or g["test_leader"]
    return savegames, test_leader


def protected_folders(savegames=None, test_leader=None):
    """Save folders that are not the harness's: [(name, path)]."""
    root, leader = _paths(savegames, test_leader)
    if not os.path.isdir(root):
        return []
    return [(d, os.path.join(root, d)) for d in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, d)) and not d.startswith(leader + "-")]


def _files(folder):
    for d, _, fs in os.walk(folder):
        for f in fs:
            p = os.path.join(d, f)
            yield os.path.relpath(p, folder).replace("\\", "/"), p


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(folder, deep=True):
    """{relative file: [size, mtime_ns, sha256 or None]}."""
    out = {}
    for rel, p in _files(folder):
        st = os.stat(p)
        out[rel] = [st.st_size, st.st_mtime_ns, _sha(p) if deep and st.st_size <= BIG else None]
    return out


def _fingerprint(man):
    return hashlib.sha256(json.dumps(sorted((k, v[0], v[2] or v[1]) for k, v in man.items())).encode()).hexdigest()[:16]


def protect(savegames=None, test_leader=None, vault=None, log=print):
    """Copy every protected save folder into the vault (skipping a state already there); keep the last KEEP_VERSIONS per folder.
    -> {folder: vault dir}. Folders are copied with their mtimes; nothing in the Savegames folder is ever written."""
    vault = vault or VAULT
    done = {}
    for name, path in protected_folders(savegames, test_leader):
        man = manifest(path)
        fp = _fingerprint(man)
        base = os.path.join(vault, name)
        dst = os.path.join(base, fp)
        if not os.path.isdir(dst):
            tmp = dst + ".part"
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.copytree(path, os.path.join(tmp, "save"), copy_function=shutil.copy2)
            json.dump({"folder": name, "taken": time.time(), "files": man}, open(os.path.join(tmp, "manifest.json"), "w"))
            os.rename(tmp, dst)
            log(f"vault: {name} -> {dst} ({sum(v[0] for v in man.values()) / 1e6:.1f} MB)")
        done[name] = dst
        versions = sorted((d for d in os.listdir(base) if not d.endswith(".part")), key=lambda d: os.path.getmtime(os.path.join(base, d)))
        for old in versions[:-KEEP_VERSIONS]:
            shutil.rmtree(os.path.join(base, old), ignore_errors=True)
    return done


def restore_hint(name=None, vault=None):
    """How to put a vaulted campaign back (nothing here ever overwrites the user's saves)."""
    vault = vault or VAULT
    return (f"Vaulted copies are in {vault}/<save folder>/<version>/save (newest version: by modified time). To restore, close the game "
            "and copy that folder's contents over the campaign's folder in Savegames/Story (keep a copy of what's there first).")


def begin_session(savegames=None, test_leader=None, vault=None, log=print):
    """Vault + baseline. The baseline is a cheap size/mtime snapshot of every protected file, compared by session_violations()."""
    root, leader = _paths(savegames, test_leader)
    protect(root, leader, vault, log)
    base = {}
    for name, path in protected_folders(root, leader):
        for rel, p in _files(path):
            st = os.stat(p)
            base[f"{name}/{rel}"] = [st.st_size, st.st_mtime_ns]
    _state["baseline"] = (root, leader, base)
    return len(base)


def session_violations():
    """Protected files that changed, appeared or vanished since begin_session(): [str]."""
    if "baseline" not in _state:
        return []
    root, leader, base = _state["baseline"]
    now = {}
    for name, path in protected_folders(root, leader):
        for rel, p in _files(path):
            try:
                st = os.stat(p)
            except OSError:
                continue
            now[f"{name}/{rel}"] = [st.st_size, st.st_mtime_ns]
    bad = [f"changed: {k}" for k in base if k in now and now[k] != base[k]]
    bad += [f"missing: {k}" for k in base if k not in now]
    bad += [f"new: {k}" for k in now if k not in base]
    return bad


def protected_leaders(savegames=None, extra=None):
    """Names that must never be the loaded host: config `game.protected_leaders`, else the leader of each protected save whose folder
    name contains HonourMode (its meta only - reading every save's meta takes minutes). Cached per process."""
    import re
    import tempfile
    from . import sources, saves
    cfg = sources.load_config()
    named = list((cfg.get("game") or {}).get("protected_leaders") or []) + list(extra or [])
    if named:
        return sorted(set(named))
    if "leaders" in _state:
        return _state["leaders"]
    root, leader = _paths(savegames, None)
    found = set()
    for name, path in protected_folders(root, leader):
        if "honour" not in name.lower():
            continue
        for f in os.listdir(path):
            if f.lower().endswith(".lsv"):
                try:
                    txt = saves.read_meta(cfg, os.path.join(path, f), tempfile.mkdtemp())
                    who = re.search(r'id="LeaderName"[^/]*value="([^"]*)"', txt)
                    if who and who.group(1):
                        found.add(who.group(1))
                except Exception:
                    pass
    _state["leaders"] = sorted(found)
    return _state["leaders"]


HARNESS_SECTIONS = {"extra", "version"}   # VerificationMods section ReflectionStrings safe for a harness save (Extra = New Mods Detected, version = a mod updated since the save: harness saves are test-only and the game just loads them with the current mods, 2026-10-13)


def classify_mod_verification(texts):
    """The Mod Verification box (shown before loading a save made with other mods) -> (kind, why).
    kind 'foreign': it lists missing or disabled mods - a campaign made with a different mod list (the user's own), never a harness
    save: do NOT press Start Game. 'harness': only version mismatches and new mods. 'unknown': no recognisable sections."""
    # the box's view model (gameui.read_mod_verification, 2026-10-12): "section:<ReflectionString>" rows (gui::DCModVerification
    # .VerificationMods, ls.VMEnumData headers) - "Extra" = New Mods Detected. Anything but known-harmless sections is not harness.
    secs = [t.split(":", 1)[1].strip().lower() for t in texts if isinstance(t, str) and t.startswith("section:")]
    if secs:
        bad = [x for x in secs if "missing" in x or "disabled" in x or "notfound" in x or "unavailable" in x]
        if bad:
            return "foreign", f"it has a {bad[0]!r} section: a campaign made with other mods"
        other = [x for x in secs if x not in HARNESS_SECTIONS]
        if other:
            return "unknown", f"unrecognised section(s) {', '.join(sorted(set(other)))}"
        return "harness", f"only {', '.join(sorted(set(secs)))} ({len(texts) - len(secs)} mods)"
    low = [t.strip().lower() for t in texts if t and t.strip()]
    missing = [i for i, t in enumerate(low) if "missing" in t and ("disabled" in t or t == "missing")]
    if missing:
        n = len(low) - missing[0] - 1
        return "foreign", f"it has a 'Missing or Disabled' section ({n} entries after it): a campaign made with other mods"
    sections = [t for t in low if t in ("version mismatch", "new mods detected", "new mods", "mismatch")]
    if sections or any("new mods detected" in t or "version mismatch" in t for t in low):
        return "harness", "only version mismatches / new mods"
    return "unknown", "no recognisable section titles"


def host_identity():
    """The loaded host's display name from the running game, or None (no session / unreadable)."""
    from . import se
    try:
        r = se.eval_lua("""
local h = Osi.GetHostCharacter()
if not h then return nil end
local e = Ext.Entity.Get(h)
local n
-- a custom character's name is CustomName.Name; DisplayName.Name is a TranslatedString object, not text (read None, 2026-10-12)
pcall(function() n = e.CustomName and e.CustomName.Name end)
if type(n) ~= "string" or n == "" then pcall(function() n = Ext.Loca.GetTranslatedString(Osi.GetDisplayName(h)) end) end
if type(n) ~= "string" or n == "" then n = nil end
return n""", "server", timeout=4)
    except Exception:
        return None
    v = r.get("result") if isinstance(r, dict) and r.get("ok") else None
    return v if isinstance(v, str) and v else None


def _alert(msg, vault=None):
    vault = vault or VAULT
    os.makedirs(vault, exist_ok=True)
    with open(os.path.join(vault, "alerts.log"), "a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")


class Watchdog(threading.Thread):
    """While the harness's game runs: kill it the moment a protected file changes or a protected leader is the host.

    It is tied to ONE game process lifetime: it acts only while the game is up, and ends the first time the game is seen to exit
    (or, if it never came up, after `grace` seconds) - a thread that outlived the session would treat the user's own later game as a
    violation and kill their campaign. Always stop it (stop_watchdog) when a launch fails."""

    def __init__(self, interval=3.0, kill=None, identity=None, deny=None, vault=None, is_up=None, grace=240.0, max_age=12 * 3600):
        super().__init__(daemon=True, name="bg3-safety-watchdog")
        self.interval, self.stop_flag, self.alert, self.vault = interval, threading.Event(), None, vault
        self.kill = kill
        self.identity = identity or host_identity
        self.deny = deny if deny is not None else protected_leaders()
        self.is_up = is_up or _game_up
        self.grace, self.max_age = grace, max_age
        self.checked = 0
        self.ended = None

    def run(self):
        born, last_id, seen = time.time(), 0.0, False
        while not self.stop_flag.wait(self.interval):
            self.checked += 1
            up = self.is_up()
            seen = seen or up
            if not up:
                if seen:
                    self.ended = "the game exited"
                    return
                if time.time() - born > self.grace:
                    self.ended = "the game never came up"
                    return
                continue
            if time.time() - born > self.max_age:
                self.ended = "max age"
                return
            bad = session_violations()
            if not bad and time.time() - last_id > self.interval * 2 and self.deny:
                last_id = time.time()
                who = self.identity()
                if who and who.strip().lower() in {d.lower() for d in self.deny}:
                    bad = [f"the loaded host is {who!r}, a protected campaign's leader"]
            if bad:
                self.alert = "; ".join(bad[:4])
                _alert("KILLED THE GAME: " + self.alert, self.vault)
                try:
                    (self.kill or _kill)()
                finally:
                    self.stop_flag.set()
                return

    def stop(self):
        self.stop_flag.set()


def _game_up():
    from . import testing
    _, g = testing.game_cfg()
    tl = testing._tasklist()
    return any(p.lower() in tl for p in g["processes"])


def _kill():
    from . import testing
    testing.kill_game(graceful=False)


DAEMON = os.path.join(VAULT, "watchdog.json")     # the detached watchdog's pid + heartbeat


def start_watchdog(**kw):
    """The watchdog for a harness game session. No arguments: a detached process (survives the caller; see the module doc). With
    arguments (tests): an in-process thread."""
    stop_watchdog()
    if kw:
        w = Watchdog(**kw)
        w.start()
        _state["watchdog"] = w
        return w
    import subprocess
    import sys
    os.makedirs(VAULT, exist_ok=True)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log = open(os.path.join(VAULT, "watchdog.log"), "a", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "bg3data.safety", "watch"], cwd=root, stdin=subprocess.DEVNULL, stdout=log,
                            stderr=subprocess.STDOUT, start_new_session=True)
    _write_daemon({"pid": proc.pid, "started": time.time(), "beat": time.time(), "checked": 0})
    t0 = time.time()
    while time.time() - t0 < 30:        # its baseline is taken before the caller launches anything
        d = daemon_state()
        if d and d.get("ready"):
            break
        if proc.poll() is not None:
            raise RuntimeError(f"the watchdog process exited at start (see {os.path.join(VAULT, 'watchdog.log')})")
        time.sleep(0.3)
    else:
        raise RuntimeError("the watchdog process didn't report ready within 30s")
    return proc.pid


def stop_watchdog():
    w = _state.get("watchdog")
    if w:
        w.stop()
    _state["watchdog"] = None
    d = daemon_state()
    if d and _alive(d.get("pid")):
        import signal
        try:
            os.kill(d["pid"], signal.SIGTERM)
        except OSError:
            pass
    if d:
        _write_daemon(dict(d, ended=d.get("ended") or "stopped"))


def _alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _write_daemon(d):
    os.makedirs(VAULT, exist_ok=True)
    tmp = DAEMON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    os.replace(tmp, DAEMON)


def daemon_state():
    try:
        return json.load(open(DAEMON, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def watchdog_running():
    """(running, description): the detached watchdog's state from its heartbeat file."""
    d = daemon_state()
    if not d:
        return False, "not running"
    if d.get("ended") or not _alive(d.get("pid")):
        return False, f"not running (last: {d.get('ended') or 'process gone'}{'; ALERT: ' + d['alert'] if d.get('alert') else ''})"
    age = time.time() - float(d.get("beat") or 0)
    return True, f"running, pid {d['pid']}, {d.get('checked', 0)} checks, last beat {age:.0f}s ago"


def _daemon_main():
    """python -m bg3data.safety watch: baseline, then watch until the game exits (or never comes up)."""
    import signal
    me = dict(daemon_state() or {}, pid=os.getpid())
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: stopping.set())
    n = begin_session(log=lambda m: None)
    w = Watchdog()
    w.start()
    me.update(ready=True, files=n, beat=time.time())
    _write_daemon(me)
    while w.is_alive() and not stopping.is_set():
        stopping.wait(2.0)
        me.update(beat=time.time(), checked=w.checked)
        _write_daemon(me)
    w.stop()
    w.join(5)
    me.update(beat=time.time(), checked=w.checked, alert=w.alert, ended=w.ended or ("stopped" if stopping.is_set() else "?"))
    _write_daemon(me)
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "watchdog ended:", me["ended"], "alert:", w.alert, flush=True)


def status(vault=None, savegames=None, test_leader=None):
    """Human-readable state: protected folders, vaulted versions, the baseline check, the watchdog, past alerts."""
    vault = vault or VAULT
    lines = []
    for name, path in protected_folders(savegames, test_leader):
        base = os.path.join(vault, name)
        vers = sorted(os.listdir(base)) if os.path.isdir(base) else []
        size = sum(os.path.getsize(p) for _, p in _files(path)) / 1e6
        lines.append(f"  {name}: {size:.1f} MB, {len(vers)} vaulted version(s)")
    w = _state.get("watchdog")
    bad = session_violations()
    out = [f"protected save folders: {len(lines)} (harness saves start with '{_paths(savegames, test_leader)[1]}-')"] + lines
    out.append("session baseline: " + ("none (no session begun)" if "baseline" not in _state else f"{len(_state['baseline'][2])} files; "
               + ("UNCHANGED" if not bad else "VIOLATIONS: " + "; ".join(bad[:5]))))
    if w and w.is_alive():
        out.append(f"watchdog: in-process, running ({w.checked} checks)" + (f"; ALERT: {w.alert}" if w.alert else ""))
    else:
        out.append("watchdog: " + watchdog_running()[1])
    al = os.path.join(vault, "alerts.log")
    if os.path.exists(al):
        out.append("past alerts:\n    " + "\n    ".join(open(al, encoding="utf-8").read().strip().splitlines()[-5:]))
    out.append(restore_hint(vault=vault))
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["watch"]:
        _daemon_main()
    else:
        print(status())
