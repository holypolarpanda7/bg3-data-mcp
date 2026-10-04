"""Drive the game's own UI (Noesis) through the Script Extender client console, for unattended restarts.

All verified in game 2026-09-30: the splash screen ("press any key") waits for raw input, so a key is posted to
the game window; the main menu's view model exposes ContinueGameCommand / QuitGame, and the quit confirmation
(Dialog_box, UUIDProperty QuitMsgID) accepts through its first action. Quitting this way is a clean exit, unlike
taskkill - a killed or hung load makes the next launch start in a no-mods safe mode.
"""
import os
import re
import time

from . import platform, se

FIND = """
local function find(n, name, d)
  if not n or d > 9 then return nil end
  local ok, nm = pcall(function() return n.Name end)
  if ok and nm == name then return n end
  local okc, cnt = pcall(function() return n.VisualChildrenCount end)
  if okc and cnt then for i = 1, cnt do local r = find(n:VisualChild(i), name, d + 1) if r then return r end end end
end
"""


def _client(code, timeout=12):
    try:
        r = se.eval_lua(code, "client", timeout=timeout)
    except (RuntimeError, TimeoutError) as e:
        return None, str(e)
    return (r["result"] if r.get("ok") else None), r


def screen(timeout=12):
    """Names of the top-level UI widgets (SplashScreen, MainMenu, Dialog_box...), or None when SE can't answer."""
    res, _ = _client(FIND + """
local out = {}
local c = find(Ext.UI.GetRoot(), "ContentRoot", 0)
if not c then return out end
for i = 1, c.VisualChildrenCount do local ok, n = pcall(function() return c:VisualChild(i).Name end) if ok and n and n ~= "" then table.insert(out, n) end end
return out""", timeout=timeout)
    return res


def loaded_modules():
    res, _ = _client("local out = {} for _, u in ipairs(Ext.Mod.GetLoadOrder()) do table.insert(out, u) end return out")
    return res


def _focus_game_script():
    """Bring the game window to the foreground (Alt tap + SetForegroundWindow). True when it is in front."""
    ps = os.path.join(os.path.dirname(__file__), "ps", "focus_game.ps1")
    r = platform.run_win(["powershell.exe" if platform.IS_WSL else "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-File", platform.to_win(ps)], timeout=30)
    return "foreground" in (r.stdout or "") and "not" not in (r.stdout or "")


def press_key(vk=0x0D):
    """Post a key to the game window (no focus needed). Dismisses the splash screen."""
    ps = os.path.join(os.path.dirname(__file__), "ps", "postkey.ps1")
    r = platform.run_win(["powershell.exe" if platform.IS_WSL else "powershell", "-ExecutionPolicy", "Bypass",
                          "-File", platform.to_win(ps), "-Vk", str(vk)], timeout=30)
    return r.returncode == 0


def _send_key_script(scan=0x2E, hold_ms=120, focus=True):
    """An OS-level key press (SendInput, hardware scan code; default 0x2E = C) with the game focused: what gameplay hotkeys
    read, unlike press_key (PostMessage) and Ext.Input (UI layer only). Types into whatever has focus - run it while
    nobody is typing."""
    ps = os.path.join(os.path.dirname(__file__), "ps", "sendkey.ps1")
    args = ["powershell.exe" if platform.IS_WSL else "powershell", "-ExecutionPolicy", "Bypass", "-File", platform.to_win(ps),
            "-Scan", str(scan), "-HoldMs", str(hold_ms)] + ([] if focus else ["-NoFocus"])
    r = platform.run_win(args, timeout=30)
    return r.returncode == 0 and "sent scan" in (r.stdout or "")


_last_shot = (960, 540)  # size of the last screenshot (half the client area)


def _click_script(x, y, right=False, count=1, shot=False):
    """An OS-level mouse click in the game's client area. (x, y) are client pixels, or with shot=True pixels of the
    last bg3_screenshot (sent as fractions of the window, so it holds at any resolution of the same aspect ratio).
    Brings the game to the front, so don't run it while someone is typing."""
    ps = os.path.join(os.path.dirname(__file__), "ps", "click.ps1")
    args = ["powershell.exe" if platform.IS_WSL else "powershell", "-ExecutionPolicy", "Bypass", "-File", platform.to_win(ps),
            "-Count", str(count)] + (["-Right"] if right else [])
    if shot:
        args += ["-Fx", f"{x / _last_shot[0]:.5f}", "-Fy", f"{y / _last_shot[1]:.5f}"]
    else:
        args += ["-X", str(int(x)), "-Y", str(int(y))]
    r = platform.run_win(args, timeout=30)
    return r.returncode == 0 and "clicked" in (r.stdout or "")


def _screenshot_script(out=None):
    """Capture the game window to a PNG (half size, ~1.5 MB) and return its path (Read it to see the screen)."""
    import tempfile
    out = out or os.path.join(tempfile.gettempdir(), "bg3_screenshot.png")
    if platform.IS_WSL:  # a Windows path the PowerShell script can write to, readable from WSL
        out = os.path.join(platform.windows_temp(), "bg3_screenshot.png")
    focus_game()  # a screen capture only sees what is visible: the game must be in front of the editor
    time.sleep(0.4)
    ps = os.path.join(os.path.dirname(__file__), "ps", "screenshot.ps1")
    r = platform.run_win(["powershell.exe" if platform.IS_WSL else "powershell", "-ExecutionPolicy", "Bypass", "-File",
                          platform.to_win(ps), "-Out", platform.to_win(out)], timeout=60)
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError("screenshot failed: " + ((r.stdout or "") + (r.stderr or "")).strip()[-300:])
    m = re.search(r"scaled (\d+)x(\d+)", r.stdout or "")
    if m:
        global _last_shot
        _last_shot = (int(m.group(1)), int(m.group(2)))
    return out


def main_menu_command(name):
    """Execute a command of the main menu's view model (ContinueGameCommand, QuitGame, OpenLoadGameDialog...)."""
    res, r = _client(FIND + f"""
local m = find(Ext.UI.GetRoot(), "MainMenu", 0)
local dc = m and m.DataContext or find(Ext.UI.GetRoot(), "ContentRoot", 0):VisualChild(1).DataContext
dc["{name}"]:Execute(nil)
return true""")
    return bool(res)


def end_turn():
    """Press the combat HUD's End Turn for the host (a scripted Osi.EndTurn doesn't end it; verified 2026-10-01)."""
    res, _ = _client(FIND + """
find(Ext.UI.GetRoot(), "ContentRoot", 0):VisualChild(1).DataContext.EndTurn:Execute(nil)
return true""")
    return bool(res)


HOST_ACTIVE = ("local tb = Ext.Entity.Get(Osi.GetHostCharacter()).TurnBased "
               "return tb ~= nil and tb.IsActiveCombatTurn == true")


def host_turn_active():
    try:
        r = se.eval_lua(HOST_ACTIVE, "server", timeout=8)
        return bool(r.get("ok") and r.get("result"))
    except (RuntimeError, TimeoutError):
        return None


HOST_TURNS = ("if not BG3T then return -1 end local n = 0 for _, e in ipairs(BG3T.events) do "
              "if e.kind == 'TurnStarted' and e.tracked == 'host' then n = n + 1 end end return n")


def _host_turns():
    try:
        r = se.eval_lua(HOST_TURNS, "server", timeout=8)
        return r.get("result") if r.get("ok") else -1
    except (RuntimeError, TimeoutError):
        return -1


def end_host_turn(timeout=90):
    """End the host's turn and wait until a NEW host turn starts (harness TurnStarted events). Summons share the
    summoner's turn, so "host turn inactive, then active" can happen before the click even lands; that read as a
    finished round and the case was verified with no round played (Faithful Hound, 2026-10-03)."""
    if not wait_host_turn(timeout):
        return False
    before = _host_turns()
    if before is None or before < 0:  # no harness events: the old inactive -> active check
        end_turn()
        t0 = time.time()
        while time.time() - t0 < 6 and host_turn_active():
            time.sleep(0.3)
        return wait_host_turn(timeout)
    end = time.time() + timeout
    clicks = 0
    while time.time() < end:
        if clicks == 0 or (host_turn_active() and time.time() - last > 6):
            end_turn()  # again if the host still has the turn 6 s after a click (it ended a summon's turn)
            clicks, last = clicks + 1, time.time()
        time.sleep(0.4)
        n = _host_turns()
        if n is not None and n > before and host_turn_active():
            return True
    return False


def wait_host_turn(timeout=60, poll=0.3):
    """Wait until it's the host's turn in combat. True when it is."""
    end = time.time() + timeout
    while time.time() < end:
        if host_turn_active():
            return True
        time.sleep(poll)
    return False


def accept_dialog(uuid_property=None):
    """Press the first (accept) action of the open message box; optionally only if its UUIDProperty matches."""
    want = f'"{uuid_property}"' if uuid_property else "nil"
    res, _ = _client(FIND + f"""
local b = find(Ext.UI.GetRoot(), "Dialog_box", 0)
if not b then return false end
local dc = b.DataContext
if {want} and dc.UUIDProperty ~= {want} then return false end
dc.Actions[1].ActionCommand:Execute(nil)
return true""")
    return bool(res)


def dialog_info(timeout=12):
    """The open message box (Dialog_box), or None: {uuid, texts (every Text in its visual tree), actions (count)}."""
    res, _ = _client(FIND + """
local b = find(Ext.UI.GetRoot(), "Dialog_box", 0)
if not b then return nil end
local texts = {}
local function walk(n, d)
  if not n or d > 14 then return end
  local ok, t = pcall(function() return n.Text end)
  if ok and type(t) == "string" and t ~= "" then table.insert(texts, t) end
  local okc, cnt = pcall(function() return n.VisualChildrenCount end)
  if okc and cnt then for i = 1, cnt do walk(n:VisualChild(i), d + 1) end end
end
walk(b, 0)
local dc = b.DataContext
local acts = 0
pcall(function() acts = #dc.Actions end)
return {uuid = tostring(dc.UUIDProperty), texts = texts, actions = acts}""", timeout=timeout)
    return res if isinstance(res, dict) else None


def dismiss_dialog(wait=3.0, timeout=12):
    """Clear a blocking acknowledge-only message box (one action, e.g. a mod/save warning). Executing the action alone
    leaves it open (seen 2026-10-03, GameMsgID), so a real Enter is posted to the game window as well. A box with
    several actions is a question for the user: reported, never answered. Returns (closed, info or None)."""
    info = dialog_info(timeout)
    if not info:
        return True, None
    if info["actions"] != 1:
        return False, info
    accept_dialog(info["uuid"])
    end = time.time() + wait
    while time.time() < end:
        time.sleep(0.5)
        if not dialog_info(timeout):
            return True, info
        press_key()
    return not dialog_info(timeout), info


def quit_game(processes, tasklist, wait=45):
    """Clean exit through Quit -> confirm. True when the game process is gone."""
    if not main_menu_command("QuitGame"):
        return False
    for _ in range(10):
        time.sleep(1)
        if accept_dialog("QuitMsgID"):
            break
    t0 = time.time()
    while time.time() - t0 < wait:
        if not any(p.lower() in tasklist() for p in processes):
            return True
        time.sleep(2)
    return False


# ---------------------------------------------------------------- level-up screen
LEVELUP_BAR = (0.34375, 0.1574)  # the LEVEL UP bar on the character sheet, as fractions of the client area (16:9 layout)


def levelup_state():
    """{sheet_open, levelup_open, complete} read from the UI tree (no screenshot needed)."""
    res, _ = _client(FIND + """
local root = Ext.UI.GetRoot()
local out = {sheet_open = find(root, "CharacterPanel", 0) ~= nil}
local w = find(root, "CharacterLevelUp", 0)
out.levelup_open = w ~= nil
if w then
  local ok, v = pcall(function() return w.DataContext.IsLevelUpComplete end)
  out.complete = ok and v == true
  local ok2, st = pcall(function() return tostring(w.DataContext.LevelUpStep) end)
  out.step = ok2 and st or nil
end
return out""")
    return res if isinstance(res, dict) else {"sheet_open": False, "levelup_open": False}


def levelup_open(sheet_scan=0x17, wait=8.0, skip_intro=True):
    """Open the level-up screen: the character sheet key (I in a default profile), then the LEVEL UP bar; then Enter skips the
    intro animation straight to the interface (it only fires while choices are pending, so it can't accept a level-up).
    Returns the state."""
    st = levelup_state()
    if st.get("levelup_open"):
        return st
    if not st.get("sheet_open"):
        send_key(sheet_scan)
        for _ in range(10):
            time.sleep(0.25)
            if levelup_state().get("sheet_open"):
                break
    click_frac(*LEVELUP_BAR)
    # The intro is dark (brightness ~0 in the sky area) and the interface bright (~180); Enter skips the intro, so press it
    # every half second until the picture is bright (it only acts while choices are pending, so it can't accept a level-up).
    t0 = time.time()
    seen_dark = False
    last_key = 0.0
    while time.time() - t0 < wait + 4:
        lum = _lum()
        if lum is not None and lum < 60:
            seen_dark = True
        if seen_dark and lum is not None and lum > 130:
            break
        if time.time() - t0 > 6 and not seen_dark and not levelup_state().get("levelup_open"):
            break                     # the bar click opened nothing: no level-up is ready
        if skip_intro and time.time() - last_key >= 0.5 and time.time() - t0 > 0.8:
            send_key(0x1C, hold_ms=100)
            last_key = time.time()
        time.sleep(0.05)
    return levelup_state()


def _lum():
    """Mean brightness (0-255) of the sky area at the top centre of the game window (the level-up intro is dark there)."""
    ok, r = _fast("lum 0.55 0.08 0.2 0.2", 5)
    try:
        return int(r.split()[1]) if ok and r.startswith("ok") else None
    except (IndexError, ValueError):
        return None


# ---------------------------------------------------------------- automatic level-up
# Geometry in client pixels (1920x1080). Pickers put their icons in one of three places; which one is in use is found by
# brightness (icons are bright on a dark panel), so no screenshot is needed.
ICON_ORIGINS = {"grid": (324, 476), "ritual": (370, 454), "savant": (324, 582)}
ICON_STEP = 46
# Feats tried in order until the Feat row clears: (name, list position, extra clicks). A taken feat can't be picked again, and
# some need an ability point or a passive ticked, hence the chain. The details panel fades in ~1 s after the click.
FEATS = [("Actor", (380, 220), []), ("Alert", (394, 246), [(1080, 316), (870, 452)]),
         ("Athlete", (394, 272), [(1080, 316)]), ("Charger", (394, 298), [])]
ACCEPT = (1174, 1004)


def pending_rows():
    """Client-pixel y of every checklist row still showing the red "!" marker (top to bottom)."""
    ok, r = _fast("redrows", 8)
    if not ok or not r.startswith("ok"):
        return None
    ys = [int(v) for v in r[2:].replace(",", " ").split()]
    out = []
    for y in ys:                      # the ring and the "!" come back as separate clusters: merge those within 30 px
        if out and y - out[-1][-1] < 30:
            out[-1].append(y)
        else:
            out.append([y])
    return [int(sum(g) / len(g)) for g in out]


def stable_pending_rows(timeout=4.0):
    """pending_rows once two reads 0.4 s apart agree (the checklist is still fading in right after the screen opens)."""
    prev = None
    end = time.time() + timeout
    while time.time() < end:
        cur = pending_rows()
        if cur is not None and cur == prev and cur:
            return cur
        prev = cur
        time.sleep(0.4)
    return pending_rows() or []


def _row_pending(y):
    """Is a checklist row near y still marked? (an unreadable helper counts as still pending; an empty list as cleared)"""
    rows = pending_rows()
    return rows is None or any(abs(v - y) < 14 for v in rows)


def _icons_at(origin):
    """True when bright icon art sits at a picker's first slot (a dark panel reads ~20, an icon 45+)."""
    x, y = origin
    ok, r = _fast("lum %.4f %.4f 0.014 0.022" % ((x - 13) / 1920, (y - 12) / 1080), 5)
    try:
        return ok and int(r.split()[1]) >= 40
    except (IndexError, ValueError):
        return False


def _fill_row(y, log, max_clicks=10):
    """Open the checklist row at y and pick icons until its marker clears. Returns True when the row cleared."""
    click(70, y, shot=False)
    time.sleep(0.5)
    kind = next((k for k, o in ICON_ORIGINS.items() if _icons_at(o)), None)
    if kind is None:                  # a text list (feat): try the chain until the row clears
        for name, pos, extra in FEATS:
            click(*pos, shot=False)
            time.sleep(1.1)
            for e in extra:
                click(*e, shot=False)
                time.sleep(0.5)
            time.sleep(0.4)
            if not _row_pending(y):
                log.append(f"row y={y}: list page, feat {name}")
                return True
        log.append(f"row y={y}: list page, no feat in the chain cleared it")
        return False
    ox, oy = ICON_ORIGINS[kind]
    clicked = 0
    for i in range(max_clicks):
        click(ox + ICON_STEP * (i % 8), oy + 44 * (i // 8), shot=False)
        clicked += 1
        time.sleep(0.35)
        if not _row_pending(y):
            log.append(f"row y={y}: {kind} picker, {clicked} icon(s)")
            return True
    log.append(f"row y={y}: {kind} picker, still pending after {clicked} icons (markers now {pending_rows()})")
    return False


def levelup_auto(feats=True, finish=True):
    """Level the host up completely: open the screen, fill every pending checklist row (spells, cantrips, rituals, savant, feat),
    then accept and wait for the level to apply. Validates as it goes: the pending markers must clear, IsLevelUpComplete must be
    true before Accept, the host level must rise by one afterwards. Returns a dict {ok, log, level_before, level_after, error}."""
    out = {"ok": False, "log": []}
    log = out["log"]
    t0 = time.time()
    out["level_before"] = host_level()
    st = levelup_state()
    if not st.get("levelup_open"):
        st = levelup_open()
    if not st.get("levelup_open"):
        out["error"] = "the level-up screen did not open (is a level-up ready? bg3_level_up grants the XP)"
        return out
    out["open_s"] = round(time.time() - t0, 1)
    stuck = set()
    stable_pending_rows()
    for _ in range(14):
        rows = [y for y in (stable_pending_rows(1.6) if not stuck else pending_rows() or []) if all(abs(y - z) > 15 for z in stuck)]
        if not rows:
            break
        y = rows[0]
        if not _fill_row(y, log):
            stuck.add(y)
            if len(stuck) > 2:
                break
    out["choices_s"] = round(time.time() - t0 - out["open_s"], 1)
    st = levelup_state()
    if not st.get("complete"):
        out["error"] = "choices still pending after the driver ran (a page type it doesn't know): " + "; ".join(log[-3:])
        return out
    if not finish:
        out["ok"] = True
        return out
    ok, msg = levelup_finish()
    out["total_s"] = round(time.time() - t0, 1)
    out["level_after"] = host_level()
    out["log"].append(msg)
    lb, la = out["level_before"], out["level_after"]
    out["ok"] = bool(ok and lb is not None and la == lb + 1)
    if not out["ok"]:
        out["error"] = f"level did not rise by one ({lb} -> {la}): {msg}"
    return out


def host_level():
    """The host character's level (server side), or None."""
    try:
        r = se.eval_lua("return Osi.GetLevel(Osi.GetHostCharacter())", "server", timeout=8)
    except (RuntimeError, TimeoutError):
        return None
    v = r.get("result") if r.get("ok") else None
    return int(v) if isinstance(v, (int, float)) else None


def levelup_finish(wait=30.0):
    """Accept a completed level-up through the screen's own FinishLevelUp command, then (wait>0) block until the level has
    really been applied (the screen goes black for several seconds first). Returns (ok, message)."""
    st = levelup_state()
    if not st.get("levelup_open"):
        return False, "the level-up screen isn't open"
    if not st.get("complete"):
        return False, "choices are still pending (IsLevelUpComplete is false): finish them first"
    before = host_level() if wait else None
    res, raw = _client(FIND + """
local w = find(Ext.UI.GetRoot(), "CharacterLevelUp", 0)
local ok, err = pcall(function() w.DataContext.FinishLevelUp:Execute(nil) end)
return {ok = ok, err = ok and "" or tostring(err)}""")
    if not (res and res.get("ok")):
        return False, (res or {}).get("err", "") if res else "no answer from the client"
    if not wait or before is None:
        return True, ""
    t0 = time.time()
    while time.time() - t0 < wait:
        time.sleep(0.5)
        lv = host_level()
        if lv is not None and lv > before:
            return True, f"level {before} -> {lv} after {time.time() - t0:.1f}s"
    return True, f"accepted, but the level hadn't risen after {wait:.0f}s"


def load_save(index=0, timeout=90.0):
    """Load a save from the pause menu of the RUNNING game (no restart): Esc, Load Game, row `index` of the list (0 = the first
    row, 34 px apart; the list is the game's own order, newest first), Load Game. Waits until a host exists in the new session
    and clears message boxes with Enter. Returns the elapsed seconds, or None on timeout."""
    t0 = time.time()
    for _ in range(3):
        if "MainMenu" in (screen() or []) or "GameMenu" in (screen() or []):
            break
        send_key(0x01, hold_ms=100)
        time.sleep(1.0)
    click(960, 568)                      # Load Game on the pause menu
    time.sleep(3.0)                      # the list fills in after a spinner
    click(320, 210 + 34 * index)
    time.sleep(0.4)
    click(1068, 1005)                    # Load Game button
    time.sleep(6.0)
    while time.time() - t0 < timeout:
        send_key(0x1C, hold_ms=60)       # the [ForceUpdate] box appears as the save starts loading
        time.sleep(1.5)
        try:
            r = se.eval_lua("return Osi.GetHostCharacter() ~= nil", "server", timeout=5)
            if r.get("ok") and r.get("result") is True:
                return round(time.time() - t0, 1)
        except (RuntimeError, TimeoutError):
            pass
    return None


# ---------------------------------------------------------------- fast input: one long-lived PowerShell helper
import atexit
import queue
import subprocess
import threading


class _InputDaemon:
    """ps/inputd.ps1 as a subprocess: compiles the Win32 glue once, then answers one-line commands (~tens of ms each)."""

    def __init__(self):
        self.p, self.q, self.lock = None, None, threading.Lock()

    def _start(self):
        ps = os.path.join(os.path.dirname(__file__), "ps", "inputd.ps1")
        kw = {"cwd": "/mnt/c"} if platform.IS_WSL and os.path.isdir("/mnt/c") else {}
        self.p = subprocess.Popen(["powershell.exe" if platform.IS_WSL else "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                   "-File", platform.to_win(ps)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1, **kw)
        self.q = queue.Queue()
        threading.Thread(target=lambda p, q: [q.put(l.strip()) for l in p.stdout], args=(self.p, self.q), daemon=True).start()
        r = self._io("ping", 40)  # the first reply waits for the one-time compile
        if r != "ok":
            raise RuntimeError("input helper didn't start: " + r)

    def _io(self, cmd, timeout):
        self.p.stdin.write(cmd + "\n")
        self.p.stdin.flush()
        return self.q.get(timeout=timeout)

    def send(self, cmd, timeout=15):
        with self.lock:
            try:
                if self.p is None or self.p.poll() is not None:
                    self._start()
                r = self._io(cmd, timeout)
            except (OSError, queue.Empty, RuntimeError, ValueError):
                self.close()
                raise
        if r.startswith("err"):
            raise RuntimeError(r)
        return r

    def close(self):
        p, self.p = self.p, None
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass


_daemon = globals().get("_daemon") or _InputDaemon()  # a hot reload keeps the running helper
atexit.register(lambda: _daemon.close())


def _fast(cmd, timeout=15):
    """Send a command to the helper; (True, reply) or (False, error) - callers fall back to the one-shot scripts."""
    try:
        return True, _daemon.send(cmd, timeout)
    except (OSError, queue.Empty, RuntimeError, ValueError) as e:
        return False, str(e)


def focus_game():
    ok, _ = _fast("focus")
    return ok or _focus_game_script()


def send_key(scan=0x2E, hold_ms=120, focus=True):
    """An OS-level key press (SendInput, hardware scan code; 0x2E = C, 0x17 = I, 0x1C = Enter, 0x01 = Esc) with the game in
    front: what gameplay hotkeys read, unlike press_key (PostMessage) and Ext.Input (UI layer only)."""
    ok, _ = _fast(f"key {int(scan)} {int(hold_ms)}")
    return ok or _send_key_script(scan, max(hold_ms, 120), focus)


def click_frac(fx, fy, right=False, count=1):
    """Click at fractions (0..1) of the game's client area."""
    ok, _ = _fast(f"click {fx:.5f} {fy:.5f} {'R' if right else 'L'} {int(count)}")
    return ok


def click(x, y, right=False, count=1, shot=False):
    """An OS-level mouse click in the game's client area. (x, y) are client pixels, or with shot=True pixels of the last
    bg3_screenshot (sent as fractions of the window: holds at any resolution of the same aspect ratio)."""
    if shot:
        if click_frac(x / _last_shot[0], y / _last_shot[1], right, count):
            return True
    else:
        ok, _ = _fast(f"clickpx {int(x)} {int(y)} {'R' if right else 'L'} {int(count)}")
        if ok:
            return True
    return _click_script(x, y, right, count, shot)


def click_many(points, shot=True, gap=0.15, right=False):
    """Several clicks in one go (e.g. a row of spell tiles); returns how many were sent."""
    sent = 0
    for x, y in points:
        if click(x, y, right, 1, shot):
            sent += 1
        time.sleep(gap)
    return sent


def screenshot(out=None):
    """Capture the game window to a PNG (half size, ~1.5 MB) and return its path (Read it to see the screen)."""
    import tempfile
    path = os.path.join(platform.windows_temp(), "bg3_screenshot.png") if platform.IS_WSL else \
        (out or os.path.join(tempfile.gettempdir(), "bg3_screenshot.png"))
    ok, r = _fast(f"shot {platform.to_win(path)}", 20)
    if ok and os.path.exists(path):
        m = re.search(r"(\d+)x(\d+) (\d+)x(\d+)", r)
        if m:
            global _last_shot
            _last_shot = (int(m.group(3)), int(m.group(4)))
        return path
    return _screenshot_script(out)
