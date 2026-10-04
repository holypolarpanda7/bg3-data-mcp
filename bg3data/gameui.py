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


def focus_game():
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


def send_key(scan=0x2E, hold_ms=120, focus=True):
    """An OS-level key press (SendInput, hardware scan code; default 0x2E = C) with the game focused: what gameplay hotkeys
    read, unlike press_key (PostMessage) and Ext.Input (UI layer only). Types into whatever has focus - run it while
    nobody is typing."""
    ps = os.path.join(os.path.dirname(__file__), "ps", "sendkey.ps1")
    args = ["powershell.exe" if platform.IS_WSL else "powershell", "-ExecutionPolicy", "Bypass", "-File", platform.to_win(ps),
            "-Scan", str(scan), "-HoldMs", str(hold_ms)] + ([] if focus else ["-NoFocus"])
    r = platform.run_win(args, timeout=30)
    return r.returncode == 0 and "sent scan" in (r.stdout or "")


_last_shot = (960, 540)  # size of the last screenshot (half the client area)


def click(x, y, right=False, count=1, shot=False):
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


def screenshot(out=None):
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
end
return out""")
    return res if isinstance(res, dict) else {"sheet_open": False, "levelup_open": False}


def levelup_open(sheet_scan=0x17, wait=6.0):
    """Open the level-up screen: the character sheet key (I in a default profile), then the LEVEL UP bar. Returns the state."""
    st = levelup_state()
    if st.get("levelup_open"):
        return st
    if not st.get("sheet_open"):
        send_key(sheet_scan)
        time.sleep(1.5)
    focus_game()
    ps = os.path.join(os.path.dirname(__file__), "ps", "click.ps1")
    platform.run_win(["powershell.exe" if platform.IS_WSL else "powershell", "-ExecutionPolicy", "Bypass", "-File",
                      platform.to_win(ps), "-Fx", str(LEVELUP_BAR[0]), "-Fy", str(LEVELUP_BAR[1])], timeout=30)
    end = time.time() + wait
    while time.time() < end:
        time.sleep(0.5)
        st = levelup_state()
        if st.get("levelup_open"):
            break
    return st


def levelup_finish():
    """Accept a completed level-up through the screen's own FinishLevelUp command. Returns (ok, message)."""
    st = levelup_state()
    if not st.get("levelup_open"):
        return False, "the level-up screen isn't open"
    if not st.get("complete"):
        return False, "choices are still pending (IsLevelUpComplete is false): finish them first"
    res, raw = _client(FIND + """
local w = find(Ext.UI.GetRoot(), "CharacterLevelUp", 0)
local ok, err = pcall(function() w.DataContext.FinishLevelUp:Execute(nil) end)
return {ok = ok, err = ok and "" or tostring(err)}""")
    return bool(res and res.get("ok")), (res or {}).get("err", "") if res else "no answer from the client"
