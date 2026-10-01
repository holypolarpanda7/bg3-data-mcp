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
