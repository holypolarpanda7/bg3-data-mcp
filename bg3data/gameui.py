"""Drive the game's own UI (Noesis) through the Script Extender client console, for unattended restarts.

All verified in game 2026-09-30: the splash screen ("press any key") waits for raw input, so a key is posted to
the game window; the main menu's view model exposes ContinueGameCommand / QuitGame, and the quit confirmation
(Dialog_box, UUIDProperty QuitMsgID) accepts through its first action. Quitting this way is a clean exit, unlike
taskkill - a killed or hung load makes the next launch start in a no-mods safe mode.
"""
import os
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


def screen():
    """Names of the top-level UI widgets (SplashScreen, MainMenu, Dialog_box...), or None when SE can't answer."""
    res, _ = _client(FIND + """
local out = {}
local c = find(Ext.UI.GetRoot(), "ContentRoot", 0)
if not c then return out end
for i = 1, c.VisualChildrenCount do local ok, n = pcall(function() return c:VisualChild(i).Name end) if ok and n and n ~= "" then table.insert(out, n) end end
return out""")
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
