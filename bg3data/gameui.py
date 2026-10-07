"""Drive the game's own UI (Noesis) through the Script Extender client console, for unattended restarts.

All verified in game 2026-09-30: the splash screen ("press any key") waits for raw input, so a key is posted to
the game window; the main menu's view model exposes ContinueGameCommand / QuitGame, and the quit confirmation
(Dialog_box, UUIDProperty QuitMsgID) accepts through its first action. Quitting this way is a clean exit, unlike
taskkill - a killed or hung load makes the next launch start in a no-mods safe mode.
"""
import os
import re
import time

from . import platform, se, timing

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
    timing.wait(0.4)
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
            timing.wait(0.3)
        return wait_host_turn(timeout)
    end = time.time() + timeout
    clicks = 0
    while time.time() < end:
        if clicks == 0 or (host_turn_active() and time.time() - last > 6):
            end_turn()  # again if the host still has the turn 6 s after a click (it ended a summon's turn)
            clicks, last = clicks + 1, time.time()
        timing.wait(0.4)
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
        timing.wait(poll)
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
        timing.wait(0.5)
        if not dialog_info(timeout):
            return True, info
        press_key()
    return not dialog_info(timeout), info


def quit_game(processes, tasklist, wait=45):
    """Clean exit through Quit -> confirm. True when the game process is gone."""
    if not main_menu_command("QuitGame"):
        return False
    for _ in range(10):
        timing.wait(1)
        if accept_dialog("QuitMsgID"):
            break
    t0 = time.time()
    while time.time() - t0 < wait:
        if not any(p.lower() in tasklist() for p in processes):
            return True
        timing.wait(2)
    return False


# ---------------------------------------------------------------- level-up screen
LEVELUP_BAR = (0.34375, 0.1574)  # the LEVEL UP bar on the character sheet, as fractions of the client area (16:9 layout)

# Level-up screen geometry, measured at 1920x1080 and sent as fractions of the client area, so it holds at any 16:9 resolution
# (not with a non-default UI scale). Pickers put their icons in one of three places; which one is in use is found by brightness.
REF_W, REF_H = 1920, 1080
CHECKLIST_X = 70                  # the left checklist: click a row here to open its page
MARKER_STRIP = (10, 70, 32, 430)  # x, y, w, h where the rows' red "!" markers are
ICON_ORIGINS = {"grid": (324, 476), "ritual": (370, 454), "savant": (324, 582)}
# Where to look for each picker's icons: (x, y, w, h), a thin band across its first slots at a height where ONLY that layout has
# art (ritual icons span y ~436-474, grid row 1 ~458-496, savant ~562-602), so the bands of neighbouring layouts never overlap.
ICON_PROBES = {"ritual": (356, 438, 112, 12), "grid": (310, 482, 112, 12), "savant": (310, 576, 112, 12)}
ICON_STEP, ICON_ROW = 46, 44
ROW_TOL = 14                      # rows are >= 22 px apart: a marker within this of y is the same row
# Feats tried in order until the Feat row clears: (name, list position, extra clicks). A taken feat can't be picked again, and
# some need an ability point or a passive ticked, hence the chain. The details panel fades in ~1 s after the click.
ABILITIES = ["Strength", "Dexterity", "Constitution", "Intelligence", "Wisdom", "Charisma"]
ASI_PLUS = (1080, 316)            # the "+" of Strength on the Ability Improvement panel; the other abilities follow 44 px apart
FEATS = [("Ability Improvement", (380, 194), "asi"),
         ("Actor", (380, 220), []), ("Alert", (394, 246), [(1080, 316), (870, 452)]),
         ("Athlete", (394, 272), [(1080, 316)]), ("Charger", (394, 298), [])]
# Multiclassing: the button at the level-up panel's top right opens "Add Class", a 4x3 grid of class tiles. The game pre-fills
# a new class's first-level choices (cantrips, Divine Order, deity, weapon mastery...), so the row loop only mops up leftovers.
ADD_CLASS_BUTTON = (708, 120)
CLASS_TILES = {n: (300 + 116 * (i % 4), 230 + 98 * (i // 4)) for i, n in enumerate(
    ["Barbarian", "Bard", "Cleric", "Druid", "Fighter", "Monk", "Paladin", "Ranger", "Rogue", "Sorcerer", "Warlock", "Wizard"])}
TILE_AREA = (280, 160, 460, 740)  # the left panel's page area, scanned for icons when no known picker layout matches
DARK, BRIGHT = 60, 130            # sky-area brightness: the intro is ~0-25, the interface ~180


def _rclick(x, y):
    """Click at reference (1920x1080) client pixels, scaled to the real window."""
    return click_frac(x / REF_W, y / REF_H)


def levelup_feats(match=None):
    """The open level-up screen's feat list, from its view model (CharacterLevelUp.DataContext.SelectableFeats - no scrolling
    needed, the visual list virtualizes): [{name, locked, requirements:[{text, met}]}]. locked = some requirement not met,
    which is how the screen decides the padlock (a mod's Feat.FeatRequirements written at runtime included; verified
    2026-10-06). match: only feats whose name contains it (case-insensitive). None if the screen or SE isn't there."""
    res, _ = _client(FIND + _VM + """
local out = {}
local col = d.SelectableFeats
for i = 1, (col and #col or 0) do
  local f = col[i]
  local reqs, locked = {}, false
  for j = 1, #f.Requirements do
    local r = f.Requirements[j]
    reqs[#reqs + 1] = {text = Ext.Loca.GetTranslatedString(r.Requirement), met = r.IsMet}
    if not r.IsMet then locked = true end
  end
  out[#out + 1] = {name = Ext.Loca.GetTranslatedString(f.Name), locked = locked, requirements = reqs}
end
return out""")
    if not isinstance(res, list):
        return None
    if match:
        res = [f for f in res if match.lower() in str(f.get("name", "")).lower()]
    return res


def levelup_state():
    """{known, sheet_open, levelup_open, complete, step, can_feat} read from the UI tree (no screenshot needed). known=False means the
    Script Extender didn't answer, so the other fields are guesses (all False) - don't act on them."""
    res, _ = _client(FIND + """
local root = Ext.UI.GetRoot()
local out = {sheet_open = find(root, "CharacterPanel", 0) ~= nil}
local w = find(root, "CharacterLevelUp", 0)
out.levelup_open = w ~= nil
if w then
  local d = w.DataContext
  local ok, v = pcall(function() return d.IsLevelUpComplete end)
  out.complete = ok and v == true
  local ok2, st = pcall(function() return tostring(d.LevelUpStep) end)
  out.step = ok2 and st or nil
  local ok3, f = pcall(function() return d.CanSelectFeat end)
  out.can_feat = ok3 and f == true
end
return out""")
    if isinstance(res, dict):
        res["known"] = True
        return res
    return {"known": False, "sheet_open": False, "levelup_open": False}


def _state(tries=3):
    """levelup_state, retried while the Script Extender doesn't answer."""
    for _ in range(tries):
        st = levelup_state()
        if st.get("known"):
            return st
        timing.wait(0.5)
    return st


def levelup_open(sheet_scan=0x17, wait=12.0):
    """Open the level-up screen: the character sheet key (I in a default profile), then the LEVEL UP bar, then wait for the
    interface. The intro (~5-6 s of animation) can't be cut short, but afterwards it may wait for input, so Enter is pressed while
    - and only while - the screen is dark: a stray Enter on the interface itself is never sent. Returns the state."""
    st = _state()
    if not st.get("known"):
        return st
    if st.get("levelup_open"):
        return st
    if not st.get("sheet_open"):
        send_key(sheet_scan)          # a toggle: only pressed when the sheet is known to be closed
        for _ in range(12):
            timing.wait(0.25)
            if levelup_state().get("sheet_open"):
                break
    # the sheet's own StartLevelUp command with the selected character: the LEVEL UP bar click often missed (the screen then
    # opened on the retry, 17 s a level), the command doesn't (2026-10-06). Then Space while the step is "Started" - the intro
    # takes it from ~2.4 s on (earlier presses are ignored) and goes SkipAnimation -> IntroComplete: ready in ~3 s, not ~7
    # wait until the game itself has the next level available (AvailableLevel > level): the command and the bar do nothing before
    # (a Choreography level 19 never opened, 2026-10-06)
    for _ in range(25):
        try:
            r = se.eval_lua("local e=Ext.Entity.Get(Osi.GetHostCharacter()) return e.AvailableLevel and e.EocLevel "
                            "and e.AvailableLevel.Level > e.EocLevel.Level", "server", timeout=8)
        except (RuntimeError, TimeoutError):
            r = {}
        if r.get("result") is True:
            break
        timing.wait(0.2)
    res = None
    for attempt in range(3):          # the command can be ignored right after the sheet opens: a few tries before the bar
        res, _ = _client(FIND + """
local w = find(Ext.UI.GetRoot(), "CharacterPanel", 0)
local d = w and w.DataContext
if not d then return false end
local ok = pcall(function() d.StartLevelUp:Execute(d.CurrentPlayer.SelectedCharacter) end)
return ok""")
        if res is not True:
            break
        opened = False
        for _ in range(10):
            timing.wait(0.15)
            if levelup_state().get("levelup_open"):
                opened = True
                break
        if opened:
            break
        res = None
    if res is True:
        t0 = time.time()
        last_key = 0.0
        while time.time() - t0 < wait:
            st = levelup_state()
            if st.get("step") == "IntroComplete":
                return _state()
            if st.get("levelup_open") and st.get("step") == "Started" and time.time() - last_key >= 0.25:
                send_key(0x39, hold_ms=60)
                last_key = time.time()
            if not st.get("levelup_open") and time.time() - t0 > 3:
                break                 # the command opened nothing: try the bar
            timing.wait(0.1)
    click_frac(*LEVELUP_BAR)
    t0 = time.time()
    seen_dark = False
    last_key = 0.0
    while time.time() - t0 < wait:
        lum = _lum()
        if lum is not None and lum < DARK:
            seen_dark = True
        if seen_dark and lum is not None and lum > BRIGHT:
            break
        # ready as soon as the checklist is drawn after the intro: the sky area doesn't always get BRIGHT, and waiting for it
        # ran into the 12 s timeout on most levels (opening took 17-22 s, 3 s when it did brighten - 2026-10-06)
        if seen_dark and lum is not None and lum >= DARK and all_rows() and levelup_state().get("levelup_open"):
            break
        if not seen_dark and time.time() - t0 > 5 and not levelup_state().get("levelup_open"):
            break                     # the bar click opened nothing: no level-up is ready
        # Space skips the intro animation (the user, 2026-10-06: Enter let it play out, 1-2 s every level); only while the
        # screen is dark, so the key never reaches the interface itself
        if seen_dark and lum is not None and lum < DARK and time.time() - last_key >= 0.2:
            send_key(0x39, hold_ms=60)
            last_key = time.time()
        timing.wait(0.05)
    return _state()


def _lum():
    """Mean brightness (0-255) of the sky area at the top centre of the game window (the level-up intro is dark there)."""
    ok, r = _fast("lum 0.55 0.08 0.2 0.2", 5)
    try:
        return int(r.split()[1]) if ok and r.startswith("ok") else None
    except (IndexError, ValueError):
        return None


def _merge_rows(ys, gap=30):
    """The ring and the "!" of one marker come back as separate clusters: merge those closer than `gap`."""
    out = []
    for y in sorted(ys):
        if out and y - out[-1][-1] < gap:
            out[-1].append(y)
        else:
            out.append([y])
    return [int(sum(g) / len(g)) for g in out]


def all_rows():
    """Reference-pixel y of every checklist row (done or pending), or None."""
    x, y, w, h = MARKER_STRIP
    ok, r = _fast("allrows %.5f %.5f %.5f %.5f" % (x / REF_W, y / REF_H, w / REF_W, h / REF_H), 8)
    if not ok or not r.startswith("ok"):
        return None
    parts = r[2:].split()
    try:
        height = int(parts[0])
        ys = [int(v) * REF_H / height for v in (parts[1].split(",") if len(parts) > 1 else [])]
    except (IndexError, ValueError, ZeroDivisionError):
        return None
    return _merge_rows(ys)


def pending_rows():
    """Reference-pixel y of every checklist row still showing the red "!" marker (top to bottom); None if unreadable."""
    x, y, w, h = MARKER_STRIP
    ok, r = _fast("redrows %.5f %.5f %.5f %.5f" % (x / REF_W, y / REF_H, w / REF_W, h / REF_H), 8)
    if not ok or not r.startswith("ok"):
        return None
    parts = r[2:].split()
    try:
        height = int(parts[0])
        ys = [int(v) * REF_H / height for v in (parts[1].split(",") if len(parts) > 1 else [])]
    except (IndexError, ValueError, ZeroDivisionError):
        return None
    return _merge_rows(ys)


def stable_pending_rows(timeout=4.0, poll=0.35):
    """pending_rows once the checklist has settled (it fades in right after the screen opens, markers appearing one by one):
    two equal non-empty reads, or three equal empty ones (a level with no choices)."""
    hist = []
    end = time.time() + timeout
    while time.time() < end:
        cur = pending_rows()
        if cur is not None:
            hist.append(cur)
            if cur and len(hist) >= 2 and hist[-2] == cur:
                return cur
            if not cur and len(hist) >= 3 and hist[-2] == hist[-3] == []:
                return []
        timing.wait(poll)
    return hist[-1] if hist else []


def _row_pending(y):
    """Is the checklist row at y still marked? (an unreadable helper counts as still pending; an empty list as cleared)"""
    rows = pending_rows()
    return rows is None or any(abs(v - y) < ROW_TOL for v in rows)


def _picker_kind():
    """Which icon picker the open page shows ("ritual", "grid", "savant"), or None for a page without icons (feat list, subclass...).
    Bands are checked in a fixed precedence - ritual, grid, savant - and the first lit one wins: a long spell grid also lights the
    savant band further down (its later rows), and no savant page lights the grid band; a page lighting the ritual band with
    many icons is a grid under a taller header."""
    for kind in ("ritual", "grid", "savant"):
        x, y, w, h = ICON_PROBES[kind]
        ok, r = _fast("lum %.5f %.5f %.5f %.5f" % (x / REF_W, y / REF_H, w / REF_W, h / REF_H), 5)
        try:
            if ok and int(r.split()[1]) >= 40:
                # Magical Secrets' header text lights the ritual band over a 317-spell grid (2026-10-05): a ritual page shows
                # a handful of icons, a grid dozens
                if kind == "ritual" and len(_tiles() or []) > 16:
                    return "grid"
                return kind
        except (IndexError, ValueError):
            pass
    return None


def _fill_row(y, log, max_clicks=10, wanted=None, wanted_only=False):
    """Open the checklist row at y and fill its page until the row's marker clears (the wanted spells first, if this page offers
    them). Returns True when it cleared."""
    _rclick(CHECKLIST_X, y)
    timing.wait(0.5)
    _SCROLLED[0] = False
    if wanted:                        # don't assume where the page is scrolled: put it at the top (probes expect that)
        _scroll_panel(down=False)
    _TAKEN.clear()
    kind = _picker_kind()
    if wanted and (kind or _stable_tiles()):
        for sid in _pick_wanted(y, kind, wanted, log):
            wanted.pop(sid, None)
        if not _row_pending(y):
            return True
    if wanted_only:
        return True
    if kind is None:
        if not levelup_state().get("can_feat"):
            for i in range(max_clicks):    # an icon page in a layout not mapped above: click what the scanner finds
                tiles = _tiles()
                if not tiles:
                    break
                _rclick(*tiles[0])
                timing.wait(0.35)
                if not _row_pending(y):
                    log.append(f"row y={y}: scanned icons, {i + 1} click(s)")
                    return True
            log.append(f"row y={y}: page the driver can't read (no known picker, no icons found, not a feat page) - not handled")
            return False
        for name, pos, extra in FEATS:
            _rclick(*pos)
            timing.wait(1.1)           # the details panel fades in
            if extra == "asi":        # +2 to the class's primary ability; past the cap of 20 the rest spills over to the next ones
                order = _asi_order()
                for ab in order:
                    for _ in range(2):
                        _rclick(ASI_PLUS[0], ASI_PLUS[1] + 44 * ABILITIES.index(ab))
                        timing.wait(0.3)
                    if not _row_pending(y):
                        log.append(f"row y={y}: feat {name} ({ab})")
                        return True
                continue
            for e in extra:
                _rclick(*e)
                timing.wait(0.5)
            timing.wait(0.4)
            if not _row_pending(y):
                log.append(f"row y={y}: feat {name}")
                return True
        log.append(f"row y={y}: feat page, no feat in the chain cleared it")
        return False
    ox, oy = ICON_ORIGINS[kind]
    for i in range(max_clicks):
        if _icon_taken(i + 1):
            continue                  # a wanted spell picked above sits here: clicking it would deselect it
        _rclick(ox + ICON_STEP * (i % 8), oy + ICON_ROW * (i // 8))
        timing.wait(0.35)
        if not _row_pending(y):
            log.append(f"row y={y}: {kind} picker, {i + 1} icon(s)")
            return True
    log.append(f"row y={y}: {kind} picker, still pending after {max_clicks} icons (markers now {pending_rows()})")
    return False


def _asi_order():
    """Abilities to raise with an Ability Score Improvement: the host class's primary ability first, then Constitution, then the
    rest (so a capped primary spills somewhere useful)."""
    try:
        r = se.eval_lua("local e = Ext.Entity.Get(Osi.GetHostCharacter()) local c = e.Classes.Classes[1] "
                        "return tostring(Ext.StaticData.Get(c.ClassUUID, 'ClassDescription').PrimaryAbility)", "server", timeout=8)
        primary = r.get("result") if r.get("ok") else None
    except (RuntimeError, TimeoutError):
        primary = None
    order = [primary] if primary in ABILITIES else []
    for ab in ["Constitution"] + ABILITIES:
        if ab not in order:
            order.append(ab)
    return order


# the level-up screen and the respec screens (CharacterFullRespec = the Oathbreaker respec; character creation for an existing character) share these view-model pieces
_VM = ('local _w = find(Ext.UI.GetRoot(), "CharacterLevelUp", 0) or find(Ext.UI.GetRoot(), "CharacterRespec", 0) '
       'or find(Ext.UI.GetRoot(), "CharacterFullRespec", 0) local d = _w.DataContext ')


def _fill_passive_selectors(log, limit=24):
    """Passive choices (manoeuvres, and other "pick N passives" lists) through the view model: TogglePassive on the first enabled,
    unselected option of any selector short of its count - preferring one that names the primary ability. One toggle per call
    (several in one frame don't all land)."""
    n = 0
    prim = (_asi_order() or [""])[0][:3].lower()
    for _ in range(limit):
        res, _ = _client(FIND + _VM + ('local PRIMARY = "%s" ' % prim) + """
local det = d.ClassProgressionDetails
for _, key in ipairs({"SubPassiveSelectors", "NotSubPassiveSelectors"}) do
  local col = det[key]
  for i = 1, (col and #col or 0) do
    local sel = col[i]
    if sel.SelectedPassiveCount < sel.MaxSelectedPassiveCount then
      -- prefer an option naming the class's primary ability (Epic Boon "+1 to an ability": EpicBoonAbility_Int for a
      -- Wizard), else the first selectable one
      local pick
      for j = 1, #sel.Passives do
        local it = sel.Passives[j]
        if it.Enabled and not it.Blocked and tonumber(it.Value) == 0 then
          pick = pick or it
          local okn, nm = pcall(function() return string.lower(tostring(it.IconName)) end)
          if PRIMARY ~= "" and okn and string.find(nm, PRIMARY, 1, true) then pick = it break end
        end
      end
      if pick then
        d.TogglePassive:Execute(pick)
        return {toggled = pick.IconName}
      end
      return {stuck = key .. "[" .. i .. "]"}
    end
  end
end
return {done = true}""")
        if not isinstance(res, dict) or res.get("done"):
            break
        if res.get("stuck"):
            log.append(f"passive selector {res['stuck']}: no selectable option left")
            break
        n += 1
        timing.wait(0.35)
    if n:
        log.append(f"passives: {n} toggled through the view model")
    return n


def _fill_skill_selectors(log, limit=12):
    """Skill choices (e.g. Bladesinger's Training in War and Song) through the view model: ClassSkills / AllSkills have
    SelectedSkillCount and MaxSelectedSkillCount; ToggleSkill on the first enabled skill the character isn't proficient in.
    One toggle per call."""
    n = 0
    for _ in range(limit):
        res, _ = _client(FIND + _VM + """
-- skill groups: ClassSkills (e.g. Bladesinger's skill), and AllSkills' ClassProficientSkills / ExpertiseSkills /
-- RaceProficientSkills (Expertise: Bard 2, Ranger 2, Rogue 1 - its options are skills the character is already proficient in)
local groups = {{"ClassSkills", d.ClassSkills, false}}
local all = d.AllSkills
if all then
  for _, k in ipairs({"ClassProficientSkills", "ExpertiseSkills", "RaceProficientSkills"}) do
    local ok, g = pcall(function() return all[k] end)
    if ok and g then groups[#groups + 1] = {k, g, k == "ExpertiseSkills"} end
  end
end
for _, gk in ipairs(groups) do
  local key, c, expertise = gk[1], gk[2], gk[3]
  local ok, short = pcall(function() return c.SelectedSkillCount < c.MaxSelectedSkillCount end)
  if ok and short then
    for i = 1, #c.Skills do
      local it = c.Skills[i]
      if it.Enabled and not it.Selected and (expertise or not it.IsProficient) and not it.IsExpert then
        d.ToggleSkill:Execute(it)
        return {toggled = key .. ":" .. tostring(it.Skill)}
      end
    end
    return {stuck = key}
  end
end
return {done = true}""")
        if not isinstance(res, dict) or res.get("done"):
            break
        if res.get("stuck"):
            log.append(f"skills {res['stuck']}: no selectable skill left")
            break
        n += 1
        log.append(f"skill {res['toggled']} chosen through the view model")
        timing.wait(0.35)
    return n


def _tiles():
    """Icon-like blobs in the page area (reference pixels), bottom row first: picked icons move UP to the "Selected" row, so the
    bottom row is always the available ones."""
    x, y, w, h = TILE_AREA
    ok, r = _fast("tiles %.5f %.5f %.5f %.5f" % (x / REF_W, y / REF_H, w / REF_W, h / REF_H), 10)
    if not ok or not r.startswith("ok"):
        return []
    parts = r[2:].split()
    try:
        height = int(parts[0])
        pts = [tuple(int(v) * REF_H / height for v in p.split(":")) for p in (parts[1].split(",") if len(parts) > 1 else [])]
    except (IndexError, ValueError, ZeroDivisionError):
        return []
    return sorted(((round(px), round(py)) for px, py in pts), key=lambda t: (-t[1], t[0]))


_TAKEN = set()                    # (item index) of icons the wanted-spell step selected on the open page
_KEEP = set()                     # handles of every spell wanted at this level (never freed to make room)


def _icon_taken(j):
    return j in _TAKEN


def _spell_items(handles):
    """Where the wanted spells sit in the open level-up's spell selectors: [(handle, selector key, selector i, item i, selected)]."""
    res, _ = _client(FIND + _VM + """
local want = {}
for _, h in ipairs(%s) do want[h] = true end
local out = {}
local det = d.ClassProgressionDetails
for _, key in ipairs({"NotSubSpellSelectors", "SubSpellSelectors"}) do
  local col = det[key]
  for i = 1, (col and #col or 0) do
    local av = col[i].Available
    for j = 1, #av do
      local it = av[j]
      local h = tostring(it.Spell.Name)
      if want[h] and not it.NotAvailable then out[#out + 1] = {h, key, i, j, it.Selected == true} end
    end
  end
end
return out""" % ("{" + ",".join('"%s"' % h for h in handles) + "}"))
    return [tuple(x) for x in res] if isinstance(res, list) else []


def _item_selected(key, i, j):
    res, _ = _client(FIND + _VM + "return d.ClassProgressionDetails.%s[%d].Available[%d].Selected == true" % (key, i, j))
    return res is True


# The spell grid can't be scrolled by the wheel, keys or properties; dragging the panel's scrollbar (x 736) works. Scrolled to the
# bottom, the grid's LAST row sits at y ~904 (verified 2026-10-03 with 181 spells); rows are 44 px apart. Candidates around it
# are each verified by click (and undone), so a few px of drift is harmless.
SCROLLBAR_X = 736
GRID_BOTTOM_Y = (904,)


def _stable_tiles(tries=4):
    """The icon scan once two scans agree (a page fades in for a moment after its row is clicked)."""
    prev = None
    for _ in range(tries):
        cur = sorted(_tiles(), key=lambda t: (t[1], t[0]))
        if cur and cur == prev:
            return cur
        prev = cur
        timing.wait(0.3)
    return prev or []


def _all_candidates(kind, j, count):
    """_icon_candidates for the detected layout first, then the other known layouts and the icon scan: the page-type probe can
    be fooled (text near a band), and every candidate is verified by click and undone if wrong."""
    seen, out = set(), []
    for k in [kind] + [x for x in ("grid", "ritual", "savant", None) if x != kind]:
        for c in _icon_candidates(k, j, count):
            if (c[1], c[2]) not in seen:
                seen.add((c[1], c[2]))
                out.append(c)
    return out


def _icon_candidates(kind, j, count):
    """Possible screen positions (reference px) of the j-th (1-based) available icon on a picker page, most likely first.
    Every candidate is checked through the item's Selected flag and undone if wrong, so extra candidates are safe."""
    if kind in ICON_ORIGINS:
        ox, oy = ICON_ORIGINS[kind]
        k = j - 1
        if kind == "grid" and k // 8 >= 9:   # below the visible rows: scroll the panel to the bottom first
            rows = -(-count // 8)
            back = rows - 1 - k // 8          # rows between this icon and the last row
            return [("scroll", ox + ICON_STEP * (k % 8), by - ICON_ROW * back) for by in GRID_BOTTOM_Y
                    if by - ICON_ROW * back > 60]
        return [("", ox + ICON_STEP * (k % 8), oy + ICON_ROW * (k // 8))]
    out = []
    # an unmapped layout (Savant at 13+, Mystic Arcanum): the available icons are a centred row (44 px apart, around x 480) -
    # Savant at y ~584, Arcanum ~476 (live 2026-10-04: 5 icons 392..566 at 476) - or the last `count` icons the scan finds.
    # The row the scan actually sees goes first: callers keep only the first candidate per guess.
    tiles = _stable_tiles()
    rows = (584, 476, 454)
    if tiles:
        seen_y = tiles[-1][1]
        rows = tuple(sorted(rows, key=lambda r: abs(r - seen_y)))
    if count and count <= 8:
        for ry in rows:
            out.append(("", round(480 + 44 * (j - (count + 1) / 2)), ry))
    if len(tiles) >= count >= j and ("", *tiles[-count:][j - 1]) not in out:
        out.insert(1 if out else 0, ("", *tiles[-count:][j - 1]))
    return out


def _selector_info(key, i, j):
    """{n, hidden, hidden_before, sel, sel_before, complete} of a spell selector, or None. The grid hides selected items (they move
    to the "Selected" row) and items whose Spell.Override is Worse or Different (live 2026-10-04: 168 items, 2 selected + 2 Worse
    + 1 Different = 5 hidden, 163 shown; the 27 Equal / NotAvailable ones are shown greyed). Bard Magical Secrets (live
    2026-10-05: 317 items, 71 NotAvailable) instead hides exactly the NotAvailable (and selected) ones and shows Worse/Different:
    na_before / na_hidden count that layout, for the other estimate."""
    res, _ = _client(FIND + _VM + """
local sel = d.ClassProgressionDetails.%s[%d]
local hb, ht, sb, st, nb, nt = 0, 0, 0, 0, 0, 0
for k = 1, #sel.Available do
  local it = sel.Available[k]
  local o = tostring(it.Spell.Override)
  local hid = it.Selected or o == "Worse" or o == "Different"
  if it.Selected then st = st + 1 if k < %d then sb = sb + 1 end end
  if hid then ht = ht + 1 if k < %d then hb = hb + 1 end end
  if it.Selected or it.NotAvailable then nt = nt + 1 if k < %d then nb = nb + 1 end end
end
return {n = #sel.Available, hidden = ht, hidden_before = hb, sel = st, sel_before = sb, na_before = nb, na_hidden = nt,
        complete = sel.IsComplete == true}""" % (key, i, j, j, j))
    return res if isinstance(res, dict) else None


def _free_slot(key, i, y, log):
    """Deselect one pick of a full selector that isn't a wanted spell, by clicking icons in the page's "Selected" row (above the
    available icons). Each click is checked: a wanted spell deselected by mistake is clicked again (restored)."""
    _want_scroll(False)
    keep = "{" + ",".join('"%s"' % h for h in _KEEP) + "}"
    state = lambda: _client(FIND + _VM + """
local sel = d.ClassProgressionDetails.%s[%d]
local keep = {}
for _, h in ipairs(%s) do keep[h] = true end
local added, kept = 0, 0
for k = 1, #sel.Available do
  local it = sel.Available[k]
  if it.Selected then added = added + 1 if keep[tostring(it.Spell.Name)] then kept = kept + 1 end end
end
return {added = added, kept = kept}""" % (key, i, keep))[0] or {}
    s0 = state()
    tiles = sorted(_stable_tiles(), key=lambda t: (t[1], t[0]))
    top = [t for t in tiles if t[1] < tiles[0][1] + 15] if tiles else []   # the topmost icon row = "Selected"
    for tx, ty in top:
        _rclick(tx, ty)
        timing.wait(0.4)
        s1 = state()
        if s1.get("added", 0) < s0.get("added", 0) and s1.get("kept", 0) == s0.get("kept", 0):
            log.append(f"row y={y}: freed a slot ({key}[{i}])")
            return True
        _rclick(tx, ty)               # restore whatever that click changed
        timing.wait(0.4)
    return False


def _pick_wanted(y, kind, wanted, log):
    """On the open page (row y), select the wanted spells this page offers. Each click is checked through the item's Selected
    flag in the view model; a click that didn't select the wanted spell is clicked again (undone), so nothing else changes.
    wanted: {spell id: handle}; returns the ids picked here."""
    got = []
    for sid, h in list(wanted.items()):
        done = False
        items = _spell_items([h])
        if any(x[4] for x in items):
            got.append(sid)
            continue
        infos = [(x, _selector_info(x[1], x[2], x[3])) for x in items]
        infos.sort(key=lambda t: bool(t[1] and t[1]["complete"]))   # selectors with room first: freeing is the last resort
        for (_, key, i, j, sel), info in infos:
            if not info:
                continue
            if info["complete"]:      # full of other picks: free one first (from the "Selected" row; verified)
                _free_slot(key, i, y, log)
                info = _selector_info(key, i, j)
                if not info or info["complete"]:
                    continue
            # selected spells leave the GRID for the "Selected" row (the icon's place counts only unselected items before it);
            # on a Savant row they stay in place - so both places are candidates (each is verified)
            # The grid hides some items (5 of 168 for a Bladesinger at 13 - not the selected ones, and no flag on the items says
            # which), so the icon's place is searched: the raw index first, then up to 10 places earlier and 2 later, each click
            # verified through Selected and undone if wrong. The best estimate (Worse/Different overrides hidden) goes first.
            est = info["hidden_before"] - info["sel_before"]
            # two layouts: greyed (NotAvailable) items shown in place, or hidden like the overridden ones - try the second's
            # estimate and its neighbours first when it differs (a 317-spell Magical Secrets grid hid 71)
            est_na = info.get("na_before", est) - info["sel_before"]
            near = [est_na, est_na - 1, est_na + 1] if est_na != est else []
            shifts = near + [h for h in sorted(set(range(-2, 11)) | {est}, key=lambda h: (abs(h - est), -h)) if h not in near]
            cands = []
            for h in shifts:
                # the icons shown: a scrolled grid is counted back from its last row, so this must be the real number
                shown = info["n"] - info.get("na_hidden", 0) if h in near else info["n"] - max(h, 0)
                if 1 <= j - h:
                    cands += [c for c in _all_candidates(kind, j - h, shown) if c not in cands][:1]
            for how, px, py in cands:
                _want_scroll(how == "scroll")
                _rclick(px, py)
                timing.wait(0.4)
                if _item_selected(key, i, j):
                    got.append(sid)
                    _TAKEN.add(j)
                    log.append(f"row y={y}: picked {sid} ({key}[{i}] #{j}{', grid scrolled' if how else ''})")
                    done = True
                    break
                _rclick(px, py)       # not this page's selector, or not this icon: undo
                timing.wait(0.4)
            if done:
                break
    _want_scroll(False)               # back to the top, where the filler clicks expect the grid
    return got


_SCROLLED = [False]                # is the left panel scrolled to the bottom right now?


def _want_scroll(down):
    """Bring the panel to the top (down=False) or bottom (down=True) if it isn't already."""
    if _SCROLLED[0] != down:
        _scroll_panel(down)
        _SCROLLED[0] = down


def _scroll_panel(down=True):
    """Drag the left panel's scrollbar to the bottom (or top)."""
    y1, y2 = (380, 1000) if down else (900, 60)
    _fast("drag %d %d %d %d" % (round(SCROLLBAR_X * _sx()), round(y1 * _sy()), round(SCROLLBAR_X * _sx()), round(y2 * _sy())), 15)
    timing.wait(0.6)


def _sx():
    return (_last_shot[0] * 2) / REF_W if _last_shot else 1.0


def _sy():
    return (_last_shot[1] * 2) / REF_H if _last_shot else 1.0


def _set_subclass(name):
    """Choose a subclass by its IDString (e.g. "BattleMaster") on a level that offers one. Returns (ok, message)."""
    res, _ = _client(FIND + _VM + """
local ids, target = {}, nil
for i = 1, #d.SelectableSubClasses do
  local it = d.SelectableSubClasses[i]
  ids[#ids + 1] = it.IDString
  if string.lower(it.IDString) == string.lower(%r) then target = it end
end
if #ids == 0 then return {err = "this level offers no subclass choice"} end
if not target then return {err = "not offered here; choices: " .. table.concat(ids, ", ")} end
d.SelectedSubClass = target
return {ok = true}""" % name)
    if not isinstance(res, dict) or not res.get("ok"):
        return False, (res or {}).get("err", "no answer") if isinstance(res, dict) else "no answer from the client"
    timing.wait(1.0)                   # read back in a later frame: the same frame still shows the old value
    res, _ = _client(FIND + _VM + "return tostring(d.SelectedSubClass and d.SelectedSubClass.IDString)")
    return (str(res).lower() == name.lower()), f"subclass now {res}"


def levelup_vm_summary():
    """Every choice the open level-up screen's view model exposes, with its fill state - to see which kind of page is unfilled."""
    res, _ = _client(FIND + _VM + """
local out = {}
local det = d.ClassProgressionDetails
for _, key in ipairs({"SubPassiveSelectors", "NotSubPassiveSelectors"}) do
  local c = det[key]
  for i = 1, (c and #c or 0) do out[#out + 1] = key .. i .. ": " .. c[i].SelectedPassiveCount .. "/" .. c[i].MaxSelectedPassiveCount .. " of " .. #c[i].Passives end
end
for _, key in ipairs({"SubSpellSelectors", "NotSubSpellSelectors"}) do
  local c = det[key]
  for i = 1, (c and #c or 0) do out[#out + 1] = key .. i .. ": added " .. c[i].AddedCount .. " complete=" .. tostring(c[i].IsComplete) .. " of " .. #c[i].Available end
end
for _, key in ipairs({"SubEquipmentSelectors", "NotSubEquipmentSelectors"}) do
  local c = det[key]
  out[#out + 1] = key .. ": " .. tostring(c and #c or 0)
end
out[#out + 1] = "abilities " .. tostring(det.SelectedAbilityCount) .. "/" .. tostring(det.MaxSelectedAbilityCount) .. " complete=" .. tostring(det.IsAbilitySelectionComplete)
local groups = {{"ClassSkills", d.ClassSkills}}
for _, k in ipairs({"ClassProficientSkills", "ExpertiseSkills", "RaceProficientSkills"}) do
  local ok, g = pcall(function() return d.AllSkills[k] end)
  if ok and g then groups[#groups + 1] = {k, g} end
end
for _, gk in ipairs(groups) do
  local ok, txt = pcall(function() return gk[1] .. ": " .. gk[2].SelectedSkillCount .. "/" .. gk[2].MaxSelectedSkillCount end)
  out[#out + 1] = ok and txt or (gk[1] .. ": ?")
end
for _, key in ipairs({"UnusedAbilityPoints", "CanSelectFeat", "IsLevelUpComplete"}) do out[#out + 1] = key .. "=" .. tostring(d[key]) end
return out""")
    return res


def class_levels():
    """{class name: level} of the host, or None."""
    try:
        r = se.eval_lua("local t = {} for _, c in ipairs(Ext.Entity.Get(Osi.GetHostCharacter()).Classes.Classes) do "
                        "t[Ext.StaticData.Get(c.ClassUUID, 'ClassDescription').Name] = c.Level end return t", "server", timeout=8)
    except (RuntimeError, TimeoutError):
        return None
    return r.get("result") if r.get("ok") and isinstance(r.get("result"), dict) else None


def subclasses():
    """{class name: subclass name or None} of the host, or None."""
    try:
        r = se.eval_lua("local t = {} for _, c in ipairs(Ext.Entity.Get(Osi.GetHostCharacter()).Classes.Classes) do "
                        "local sd = c.SubClassUUID and Ext.StaticData.Get(c.SubClassUUID, 'ClassDescription') "
                        "t[Ext.StaticData.Get(c.ClassUUID, 'ClassDescription').Name] = sd and sd.Name or false end return t",
                        "server", timeout=8)
    except (RuntimeError, TimeoutError):
        return None
    return r.get("result") if r.get("ok") and isinstance(r.get("result"), dict) else None


def spell_offers():
    """What the open level-up screen offers in each spell choice: [{key, i, total, levels: {spell level: n}}] - the ground truth
    a static check can only predict (lists change at load through MergedInto: dnd55e's one-level lists offer every level so far,
    verified 2026-10-05)."""
    res, _ = _client(FIND + _VM + """
local out = {}
local det = d.ClassProgressionDetails
for _, key in ipairs({"NotSubSpellSelectors", "SubSpellSelectors"}) do
  local col = det[key]
  for i = 1, (col and #col or 0) do
    local av, lv = col[i].Available, {}
    for j = 1, #av do  -- greyed-out spells (NotAvailable: above the character's slots, or known) can't be picked
      if not av[j].NotAvailable then local L = tostring(av[j].Spell.Level) lv[L] = (lv[L] or 0) + 1 end
    end
    out[#out + 1] = {key = key, i = i, total = #av, levels = lv}
  end
end
return out""")
    return res if isinstance(res, list) else []


def screen_choices():
    """The choice rows the open level-up screen shows, from its view model: {class|sub: {passives: [max picks], spells: [n spell
    choices' sizes], skills: n}, feat: bool} - compared with the level's progression selectors by testing.screen_vs_data
    (2026-10-06: the level-up screen had no check of what it offers, only of what got applied)."""
    res, _ = _client(FIND + _VM + """
local det = d.ClassProgressionDetails
-- rows left from the subclass shown before the pick stay in the collections as UpdateState "Old" and aren't on screen (Fighter 3:
-- Eldritch Knight's cantrip and spell rows for a Champion, 2026-10-06)
local function old(sel) local ok, st = pcall(function() return tostring(sel.UpdateState) end) return ok and st == "Old" end
local out = {class = {passives = {}, spells = {}}, sub = {passives = {}, spells = {}}, feat = d.CanSelectFeat and true or false}
for _, k in ipairs({{"NotSubPassiveSelectors", "class"}, {"SubPassiveSelectors", "sub"}}) do
  local c = det[k[1]]
  for i = 1, (c and #c or 0) do if not old(c[i]) then table.insert(out[k[2]].passives, c[i].MaxSelectedPassiveCount) end end
end
for _, k in ipairs({{"NotSubSpellSelectors", "class"}, {"SubSpellSelectors", "sub"}}) do
  local c = det[k[1]]
  for i = 1, (c and #c or 0) do if not old(c[i]) then table.insert(out[k[2]].spells, #c[i].Available) end end
end
return out""")
    return res if isinstance(res, dict) else None


def offer_gaps(offers, top_slot, lists=None):
    """Problems with the spell choices a level-up screen offered, as "FAIL ..." / "WARN ..." lines.
    FAIL: a level-up's class spell picks, taken together, skip a spell level the
    character can cast - leveled spells offered, but not every level from 1 to the highest offered one within the slots
    (Apotheosis Sorcerer 13 offered only level 7, 2026-10-05). Taken together because dnd55e often splits one level-up's
    picks by spell level. Subclass picks get WARN for the same gap: they're feature lists (Evocation Savant 13 offers only
    level 7 by the rules; Divine Soul 3 picks from the level 1 and level 2 Cleric lists).
    WARN: one pick that offers castable spells and spells above the character's highest slot (dnd55e's Ritual Caster Feat
    list offers 3rd-level rituals at Wizard 4, 2026-10-06). A pick entirely above the slots (slot-free, e.g. Mystic
    Arcanum) or of cantrips only is exempt from both.
    lists: {(key, i): (list uuid, selector id, list name)} from the level's SelectSpells, to name the picks."""
    bad, groups = [], {}
    def label(o):
        uuid_, sid, lname = (lists or {}).get((o["key"], o["i"]), (None, None, None))
        return f"{o['key']}[{o['i']}]" + (f" ({lname or uuid_}{', ' + sid if sid else ''})" if uuid_ else "")
    for o in offers or []:
        lv = sorted(int(k) for k in (o.get("levels") or {}) if str(k).lstrip("-").isdigit() and int(k) > 0)
        if not lv or (top_slot and min(lv) > top_slot):
            continue
        castable = [x for x in lv if not top_slot or x <= top_slot]
        above = [x for x in lv if top_slot and x > top_slot]
        if above:
            bad.append(f"WARN {label(o)} offers spell levels {lv}: {', '.join(map(str, above))} above the highest slot ({top_slot})")
        g = groups.setdefault(o["key"], [set(), []])
        g[0].update(castable)
        g[1].append(label(o))
    for key, (lv, names) in groups.items():
        missing = [x for x in range(1, max(lv) + 1) if x not in lv] if lv else []
        if missing:
            # subclass picks are feature-shaped by design (Evocation Savant: one spell of the NEW slot level only; Divine Soul's
            # per-level Cleric picks), so a gap there is a WARN; class picks are learn lists and must cover every level
            sev = "WARN" if key == "SubSpellSelectors" else "FAIL"
            bad.append(f"{sev} {key} picks offer spell levels {sorted(lv)}: no level {', '.join(map(str, missing))} spells "
                       f"({'; '.join(names)})")
    return bad


def levelup_auto(finish=True, add_class=None, subclass=None, spells=None):
    """Level the host up completely: open the screen, (add_class: take the level in that class instead - a multiclass, or another
    level of a second class), fill every pending checklist row (spells, cantrips, rituals, savant, feat / ability improvement),
    then accept and wait for the level to apply. Validates as it goes: each row's marker must clear, IsLevelUpComplete must be
    true before Accept, the host level must rise by exactly one (and with add_class, that class's level by one). Returns {ok, log,
    level_before, level_after, classes_before, classes_after, open_s, choices_s, total_s, error}. subclass: on a level that offers a
    subclass choice, take this one (its IDString, e.g. "BattleMaster"; verified on the character afterwards) - otherwise the game's
    default (the first in its list) is kept. spells: {spell id: DisplayName handle} to learn at this level (the rest are filler);
    each is confirmed selected before Accept, or the level-up stops unaccepted."""
    out = {"ok": False, "log": []}
    log = out["log"]
    t0 = time.time()
    out["level_before"] = host_level()
    if out["level_before"] is None:
        out["error"] = "no host level from the Script Extender (is a game loaded?)"
        return out
    st = levelup_open()
    if st.get("known") and not st.get("levelup_open"):   # the bar click can miss while the sheet is still settling: once more
        send_key(0x01, hold_ms=100)
        timing.wait(1.5)
        st = levelup_open()
    if not st.get("known"):
        out["error"] = "the Script Extender didn't answer while opening the level-up screen"
        return out
    if not st.get("levelup_open"):
        out["error"] = "the level-up screen did not open (is a level-up ready? bg3_level_up grants the XP)"
        return out
    out["open_s"] = round(time.time() - t0, 1)
    if add_class:
        if add_class not in CLASS_TILES:
            out["error"] = f"unknown class {add_class!r} (one of {', '.join(CLASS_TILES)})"
            return out
        out["classes_before"] = owned = class_levels() or {}
        if add_class in owned:
            # A class the character has is greyed out on Add Class; it's levelled from the class carousel at the panel's top,
            # which lists the owned classes ALPHABETICALLY (verified 2026-10-03: Wizard, Cleric, Barbarian, Fighter taken in
            # that order -> Barbarian, Cleric, Fighter, Wizard). The level check after Accept catches a wrong pick.
            # One command per call with a pause: several in one frame only move the carousel once (seen 2026-10-03).
            steps = sorted(owned).index(add_class)
            for cmd in ["SelectFirstUsedClass"] + ["SelectNextUsedClass"] * steps:
                _client(FIND + 'local d = find(Ext.UI.GetRoot(), "CharacterLevelUp", 0).DataContext d.%s:Execute(nil) return true' % cmd)
                timing.wait(0.6)
            timing.wait(0.9)
            log.append(f"levelled owned class {add_class} (carousel position {steps + 1} of {len(owned)})")
        else:
            _rclick(*ADD_CLASS_BUTTON)
            timing.wait(1.2)
            _rclick(*CLASS_TILES[add_class])
            timing.wait(1.5)           # the class's first-level picks are pre-filled as it switches
            log.append(f"added class {add_class}")
    if subclass:
        ok, msg = _set_subclass(subclass)
        log.append(f"subclass {subclass}: {msg}")
        if not ok:
            out["error"] = f"couldn't choose subclass {subclass}: {msg}"
            return out
    out["offers"] = spell_offers()  # before any pick changes the lists
    _fill_passive_selectors(log)
    _fill_skill_selectors(log)
    wanted = dict(spells or {})
    _KEEP.clear()
    _KEEP.update(wanted.values())
    stuck = []
    rows = stable_pending_rows()
    for _ in range(14):
        rows = [y for y in rows if all(abs(y - z) > ROW_TOL for z in stuck)]
        if not rows:
            break
        if not _fill_row(rows[0], log, wanted=wanted):
            stuck.append(rows[0])
            if len(stuck) > 2:
                break
        rows = pending_rows() or []
    if wanted:                        # wanted spells not offered on a pending row: they sit on rows already filled - visit those
        for ry in (all_rows() or [])[1:]:     # the first row is "Level Up <class>"
            if not wanted:
                break
            _fill_row(ry, log, wanted=wanted, wanted_only=True)
        for _ in range(6):            # a slot freed for a wanted spell may have left its row short: fill it again
            rows = pending_rows() or []
            if not rows:
                break
            if not _fill_row(rows[0], log):
                break
    out["choices_s"] = round(time.time() - t0 - out["open_s"], 1)
    if spells:
        have = {h for h, *_rest, sel in _spell_items(list(spells.values())) if sel}
        missing = [sid for sid, h in spells.items() if h not in have]
        if missing:
            out["error"] = "wanted spells not selected (left unaccepted): " + ", ".join(missing)
            return out
        log.append("wanted spells selected: " + ", ".join(spells))
    if not _state().get("complete"):
        dead, _ = _client(FIND + _VM + """
local out = {}
local det = d.ClassProgressionDetails
for _, key in ipairs({"NotSubSpellSelectors", "SubSpellSelectors"}) do
  local c = det[key]
  for i = 1, (c and #c or 0) do
    local sel, open = c[i], 0
    for j = 1, #sel.Available do if not sel.Available[j].NotAvailable then open = open + 1 end end
    if #sel.Available > 0 and open == 0 and sel.AddedCount == 0 then out[#out + 1] = key .. i .. " (" .. #sel.Available .. " spells)" end
  end
end
return out""")
        why = ("; a spell choice offers no selectable spell - every option is unavailable (e.g. spells above the character's slot "
               "levels without the slot-free selector form), so the game can't finish this level-up: " + ", ".join(dead)) if dead else ""
        out["error"] = "choices still pending after the driver ran: " + "; ".join(log[-3:] or ["no pending rows were found"]) + why
        return out
    # read last: right after a subclass pick the view model can still hold the default subclass's rows (Fighter 3 showed Eldritch
    # Knight's two spell choices for a Champion, 2026-10-06)
    out["screen"] = screen_choices()
    if not finish:
        out["ok"] = True
        return out
    ok, msg = levelup_finish()
    out["total_s"] = round(time.time() - t0, 1)
    out["level_after"] = host_level()
    log.append(msg)
    lb, la = out["level_before"], out["level_after"]
    out["ok"] = bool(ok and la == lb + 1)
    if not out["ok"]:
        out["error"] = f"level did not rise by one ({lb} -> {la}): {msg}"
    if out["ok"] and subclass:
        subs = subclasses() or {}
        got = [v for v in subs.values() if v and str(v).lower() == subclass.lower()]
        if not got:
            out["ok"] = False
            out["error"] = f"subclass {subclass} isn't on the character after the level-up: {subs}"
    if out["ok"] and add_class:
        out["classes_after"] = class_levels()
        before = (out.get("classes_before") or {}).get(add_class, 0)
        after = (out["classes_after"] or {}).get(add_class)
        if after != before + 1:
            out["ok"] = False
            out["error"] = f"the level went to another class: {add_class} {before} -> {after} ({out['classes_after']})"
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
    st = _state()
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
    if not wait:
        return True, ""
    if before is None:
        return False, "accepted, but the level before was unreadable, so the apply can't be confirmed"
    t0 = time.time()
    while time.time() - t0 < wait:
        timing.wait(0.5)
        lv = host_level()
        if lv is not None and lv > before:
            return True, f"level {before} -> {lv} after {time.time() - t0:.1f}s"
    return False, f"accepted, but the level hadn't risen after {wait:.0f}s"


def _pause_menu():
    """Bring up the pause menu (GameMenu), pressing Esc as needed; False if it can't be confirmed (then nothing is clicked)."""
    for _ in range(7):                # level-up page -> level-up -> sheet -> pause menu can take several
        names = screen()
        if names is None:
            return False
        if "GameMenu" in names:
            return True
        send_key(0x01, hold_ms=100)
        timing.wait(1.0)
    return False


def _close_menus():
    for _ in range(4):
        names = screen() or []
        if not any(n in names for n in ("GameMenu", "SaveLoad", "Save", "Load")):
            return
        send_key(0x01, hold_ms=100)
        timing.wait(0.8)


def _lua_str(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def load_save(index=0, timeout=90.0, name=None):
    """Load a save from the pause menu of the RUNNING game (no restart): Esc until the pause menu (GameMenu) is confirmed open,
    Load Game, the save, Load Game. `name`: the save's title (exact, else a unique substring, e.g. "Barbarian L1 Base"), selected
    through the Load screen's view model; else row `index` (0 = first, the newest). Waits until a host exists in the new
    session, clearing message boxes with Enter. Returns (seconds, host level), or (None, reason)."""
    t0 = time.time()
    # the HUD's own OpenLoadGameDialog command: no Esc / pause-menu clicks (2026-10-06: Esc stopped reaching the game after a
    # level-up session and every load failed with "the pause menu didn't open")
    res, _ = _client(FIND + """
local h = find(Ext.UI.GetRoot(), "HotBar", 0)
local ok = h and pcall(function() h.DataContext.OpenLoadGameDialog:Execute(nil) end)
return ok and true or false""")
    opened = False
    if res is True:
        for _ in range(30):
            timing.wait(0.2)
            n, _ = _client(FIND + 'local w = find(Ext.UI.GetRoot(), "LoadGame", 0) return w ~= nil and #w.DataContext.ExistingSaves or 0')
            if isinstance(n, (int, float)) and n > 0:
                opened = True
                break
    if not opened:
        if not _pause_menu():
            return None, "the pause menu didn't open (nothing was clicked)"
        _rclick(960, 568)             # Load Game
        timing.wait(3.0)               # the list fills in after a spinner
    if name:
        # select it in the Load screen's view model (gui::DCSavegames.SelectedSave via SetProperty): the list shows ~22 rows, so
        # a row click missed any older save once checkpoints pushed it down and the NEWEST save loaded instead (2026-10-05)
        res, _ = _client(FIND + """
local d = find(Ext.UI.GetRoot(), "LoadGame", 0).DataContext
local want, exact, part = string.lower(%s), {}, {}
local saves = d.ExistingSaves
for i = 1, #saves do
  local t = string.lower(tostring(saves[i].Title))
  if t == want then exact[#exact + 1] = saves[i] elseif string.find(t, want, 1, true) then part[#part + 1] = saves[i] end
end
local hits = #exact > 0 and exact or part
if #hits ~= 1 then
  local names = {}
  for _, s in ipairs(hits) do names[#names + 1] = tostring(s.Title) end
  return {n = #hits, names = names}
end
d:SetProperty("SelectedSave", hits[1])
-- the Load Game button's own command: a click loads the row the LIST has selected, which the view model property doesn't move
-- (2026-10-06: a save in another part of the list never loaded, and the newest save's running session passed as loaded)
local function f(n, dd)
  if dd > 12 then return nil end
  local ok, nm = pcall(function() return n.Name end)
  if ok and nm == "LoadSaveBtn" then return n end
  local okc, cnt = pcall(function() return n.VisualChildrenCount end)
  if okc and cnt then for i = 1, cnt do local r = f(n:VisualChild(i), dd + 1) if r then return r end end end
end
local b = f(find(Ext.UI.GetRoot(), "LoadGame", 0), 0)
local ok = b and pcall(function() b.Command:Execute(d.SelectedSave) end)
return {n = 1, selected = tostring(d.SelectedSave.Title), executed = ok and true or false}""" % _lua_str(name))
        if not isinstance(res, dict) or res.get("n") != 1:
            send_key(0x01, hold_ms=60)
            return None, f"{(res or {}).get('n', '?')} saves match {name!r}: {', '.join((res or {}).get('names') or [])}"
        if not res.get("executed"):
            _rclick(1068, 1005)       # Load Game button (fallback)
    else:
        _rclick(320, 210 + 34 * index)
        timing.wait(0.4)
        _rclick(1068, 1005)           # Load Game button
    # the running session has to go away first: until it does, a host still answers (the old one). No Enter before that - a Mod
    # Verification box (the save's mod versions differ from the load order) takes Enter as Start Game with its Downgrade ticked
    gone = False
    while time.time() - t0 < 40:
        timing.wait(1.0)
        if host_level() is None:
            gone = True
            break
    if not gone:
        shown = screen() or []
        return None, (f"the load didn't start - the old session is still running (screen: {', '.join(shown)}); a Mod Verification "
                      "box means the save's mod list differs from the load order - fix it with bg3_save_fix_mods")
    while time.time() - t0 < timeout:
        send_key(0x1C, hold_ms=60)    # the [ForceUpdate] box appears as the save starts loading
        timing.wait(1.5)
        lv = host_level()
        if lv is not None:
            # the [ForceUpdate] box can still come up after the host exists, and it blocks the console: clear it
            for _ in range(6):
                timing.wait(1.0)
                if not dialog_info():
                    break
                dismiss_dialog()
            return round(time.time() - t0, 1), lv
    return None, f"no host after {timeout:.0f}s"


def save_game(name, timeout=40.0):
    """Save the running game under `name` (pause menu -> Save Game -> New Save -> name -> Save) and wait until the save folder
    exists. Returns (ok, folder or reason)."""
    from . import saves, sources
    cfg = sources.load_config()
    before = {d for _m, d, _p in saves.list_saves(cfg, 500)}
    if not _pause_menu():
        return False, "the pause menu didn't open (nothing was clicked)"
    _rclick(960, 524)                 # Save Game
    timing.wait(2.5)
    _rclick(608, 200)                 # New Save
    timing.wait(1.2)
    _rclick(958, 738)                 # the description field
    timing.wait(0.4)
    _fast("chord 29 30")              # Ctrl+A: replace the default description
    timing.wait(0.2)
    _fast("text " + name)
    timing.wait(0.4)
    _rclick(1066, 862)                # Save
    t = time.time()
    while time.time() - t < timeout:
        timing.wait(1.5)
        new = [d for _m, d, _p in saves.list_saves(cfg, 500) if d not in before]
        if any(name.lower() in d.lower() for d in new):
            timing.wait(2.0)           # let the write finish
            _close_menus()
            return True, next(d for d in new if name.lower() in d.lower())
    _close_menus()
    return False, f"no new save named {name!r} after {timeout:.0f}s"


RESPEC_CLASS_TILES = {n: (294 + 124 * (i % 4), 176 + 124 * (i // 4)) for i, n in enumerate(
    ["Barbarian", "Bard", "Cleric", "Druid", "Fighter", "Monk", "Paladin", "Ranger", "Rogue", "Sorcerer", "Warlock", "Wizard"])}
# Classes from mods sit in a fourth row, below the visible grid: drag the class list's scrollbar down first, then the row is at
# y ~432 (verified 2026-10-04 with dnd55e's Artificer, Gunslinger, Illrigger, Monster Hunter)
RESPEC_MORE_TILES = {n: (294 + 124 * i, 432) for i, n in enumerate(["Artificer", "Gunslinger", "Illrigger", "MonsterHunter"])}
RESPEC_SCROLL = (732, 200, 732, 470)
RESPEC_CONFIRM = (1196, 1032)


def respec(cls, timeout=20.0):
    """Turn the host into a level 1 `cls` through the game's respec (Osi.StartRespec - what Withers does): pick the class tile;
    the game pre-fills that class's choices (abilities rearranged for it, cantrips, spells, weapon mastery); CONFIRM. Race,
    background, name and XP stay. Verified afterwards: the host is exactly {cls: 1}. Returns (ok, message)."""
    if cls not in RESPEC_CLASS_TILES and cls not in RESPEC_MORE_TILES:
        return False, f"unknown class {cls!r}"
    _close_menus()
    r = se.eval_lua("local ok, e = pcall(function() Osi.StartRespec(Osi.GetHostCharacter()) end) return ok", "server", timeout=10)
    if not (r.get("ok") and r.get("result")):
        return False, "Osi.StartRespec failed"
    t = time.time()
    while time.time() - t < timeout and "CharacterRespec" not in (screen() or []):
        timing.wait(0.5)
    if "CharacterRespec" not in (screen() or []):
        return False, "the respec screen didn't open"
    timing.wait(1.0)
    if cls in RESPEC_MORE_TILES:
        _fast("drag %d %d %d %d" % RESPEC_SCROLL, 15)
        timing.wait(0.8)
        _rclick(*RESPEC_MORE_TILES[cls])
    else:
        _rclick(*RESPEC_CLASS_TILES[cls])
    timing.wait(1.5)
    # most classes come pre-filled; some don't (Artificer cantrips, Monster Hunter weapon mastery) - fill them like a level-up
    log = []
    _fill_passive_selectors(log)
    _fill_skill_selectors(log)
    for _ in range(6):
        rows = pending_rows() or []
        if not rows:
            break
        _fill_row(rows[0], log)
    if pending_rows():
        return False, f"{cls}'s first-level choices couldn't all be filled (rows {pending_rows()}; {log}) - not confirmed"
    _rclick(*RESPEC_CONFIRM)
    t = time.time()
    while time.time() - t < timeout and "CharacterRespec" in (screen() or ["CharacterRespec"]):
        timing.wait(0.5)
    timing.wait(1.0)
    got = class_levels()
    return (got == {cls: 1}), f"host is now {got}"


RESPEC_OATHBREAKER_ACCEPT = (1180, 1004)


def respec_oathbreaker(timeout=25.0):
    """Turn the host Paladin into an Oathbreaker the way the game does once an oath is broken and the Oathbreaker Knight's offer is
    taken (base Shared GLO_PaladinOathbreaker: StartRespecToOathbreaker). The subclass picker never offers Oathbreaker (it's tagged
    OATHBREAKER), so a test build picks another oath and switches here. The screen (CharacterFullRespec) comes pre-filled (subclass
    + prepared spells, verified 2026-10-07 at Paladin 5); fill anything left, Accept, and check the subclass. Straight after a
    level-up the first try once didn't take, so it waits for the level-up screen to go and retries. Returns (ok, message)."""
    sub_lua = ("local c = Ext.Entity.Get(Osi.GetHostCharacter()).Classes.Classes[1] local s = c.SubClassUUID and "
               "Ext.StaticData.Get(c.SubClassUUID, 'ClassDescription') return s and s.Name or ''")
    t = time.time()
    while time.time() - t < timeout and "CharacterLevelUp" in (screen() or []):
        timing.wait(0.5)
    _close_menus()
    timing.wait(1.0)
    sub, log = "", []
    for attempt in range(3):
        if "CharacterFullRespec" not in (screen() or []):
            r = se.eval_lua("local ok = pcall(function() Osi.StartRespecToOathbreaker(Osi.GetHostCharacter()) end) return ok",
                            "server", timeout=10)
            if not (r.get("ok") and r.get("result")):
                return False, "Osi.StartRespecToOathbreaker failed"
            t = time.time()
            while time.time() - t < timeout and "CharacterFullRespec" not in (screen() or []):
                timing.wait(0.5)
            if "CharacterFullRespec" not in (screen() or []):
                log.append(f"try {attempt + 1}: the respec screen didn't open")
                continue
        timing.wait(3.0)  # the screen fades in; an early click is lost
        for _ in range(6):
            rows = pending_rows() or []
            if not rows:
                break
            _fill_row(rows[0], log)
        if pending_rows():
            return False, f"the Oathbreaker respec choices couldn't all be filled (rows {pending_rows()}; {log}) - not accepted"
        _rclick(*RESPEC_OATHBREAKER_ACCEPT)
        t = time.time()
        while time.time() - t < 10 and "CharacterFullRespec" in (screen() or ["CharacterFullRespec"]):
            timing.wait(0.5)
        timing.wait(1.5)
        sub = se.eval_lua(sub_lua, "server", timeout=10).get("result")
        if sub == "Oathbreaker":
            return True, "subclass now Oathbreaker" + (f" (after {attempt + 1} tries)" if attempt else "")
        log.append(f"try {attempt + 1}: subclass {sub or 'none'}")
    return False, f"subclass now {sub or 'none'} ({'; '.join(log)})"


# ---------------------------------------------------------------- fast input: one long-lived PowerShell helper
import atexit
import queue
import subprocess
import threading


class _InputDaemon:
    """ps/inputd.ps1 as a subprocess: compiles the Win32 glue once, then answers one-line commands (~tens of ms each)."""

    SCRIPT = os.path.join(os.path.dirname(__file__), "ps", "inputd.ps1")

    def __init__(self):
        self.p, self.q, self.lock, self.mtime = None, None, threading.Lock(), None

    def _stale(self):
        """The script changed since this helper started (an edit + hot reload): its commands may differ, so restart it."""
        try:
            return self.mtime is not None and os.path.getmtime(self.SCRIPT) != self.mtime
        except OSError:
            return False

    def _start(self):
        ps = self.SCRIPT
        self.mtime = os.path.getmtime(ps)
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
                if self.p is not None and self._stale():
                    self.close()
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


# A hot reload keeps the running helper (it restarts by itself when inputd.ps1 changed); an instance of the old class is replaced.
_daemon = globals().get("_daemon")
if not hasattr(_daemon, "_stale"):
    if _daemon is not None:
        _daemon.close()
    _daemon = _InputDaemon()
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
        timing.wait(gap)
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
