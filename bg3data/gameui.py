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
        time.sleep(0.5)
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
            time.sleep(0.25)
            if levelup_state().get("sheet_open"):
                break
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
        if not seen_dark and time.time() - t0 > 5 and not levelup_state().get("levelup_open"):
            break                     # the bar click opened nothing: no level-up is ready
        if seen_dark and lum is not None and lum < DARK and time.time() - last_key >= 0.5:
            send_key(0x1C, hold_ms=100)
            last_key = time.time()
        time.sleep(0.05)
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
        time.sleep(poll)
    return hist[-1] if hist else []


def _row_pending(y):
    """Is the checklist row at y still marked? (an unreadable helper counts as still pending; an empty list as cleared)"""
    rows = pending_rows()
    return rows is None or any(abs(v - y) < ROW_TOL for v in rows)


def _picker_kind():
    """Which icon picker the open page shows ("ritual", "grid", "savant"), or None for a page without icons (feat list, subclass...).
    A band reads ~20 on the dark panel and 45+ across icon art; the brightest band above the threshold wins."""
    best, best_lum = None, 40
    for kind, (x, y, w, h) in ICON_PROBES.items():
        ok, r = _fast("lum %.5f %.5f %.5f %.5f" % (x / REF_W, y / REF_H, w / REF_W, h / REF_H), 5)
        try:
            v = int(r.split()[1]) if ok else 0
        except (IndexError, ValueError):
            v = 0
        if v >= best_lum:
            best, best_lum = kind, v
    return best


def _fill_row(y, log, max_clicks=10):
    """Open the checklist row at y and fill its page until the row's marker clears. Returns True when it cleared."""
    _rclick(CHECKLIST_X, y)
    time.sleep(0.5)
    kind = _picker_kind()
    if kind is None:
        if not levelup_state().get("can_feat"):
            for i in range(max_clicks):    # an icon page in a layout not mapped above: click what the scanner finds
                tiles = _tiles()
                if not tiles:
                    break
                _rclick(*tiles[0])
                time.sleep(0.35)
                if not _row_pending(y):
                    log.append(f"row y={y}: scanned icons, {i + 1} click(s)")
                    return True
            log.append(f"row y={y}: page the driver can't read (no known picker, no icons found, not a feat page) - not handled")
            return False
        for name, pos, extra in FEATS:
            _rclick(*pos)
            time.sleep(1.1)           # the details panel fades in
            if extra == "asi":        # +2 to the class's primary ability; past the cap of 20 the rest spills over to the next ones
                order = _asi_order()
                for ab in order:
                    for _ in range(2):
                        _rclick(ASI_PLUS[0], ASI_PLUS[1] + 44 * ABILITIES.index(ab))
                        time.sleep(0.3)
                    if not _row_pending(y):
                        log.append(f"row y={y}: feat {name} ({ab})")
                        return True
                continue
            for e in extra:
                _rclick(*e)
                time.sleep(0.5)
            time.sleep(0.4)
            if not _row_pending(y):
                log.append(f"row y={y}: feat {name}")
                return True
        log.append(f"row y={y}: feat page, no feat in the chain cleared it")
        return False
    ox, oy = ICON_ORIGINS[kind]
    for i in range(max_clicks):
        _rclick(ox + ICON_STEP * (i % 8), oy + ICON_ROW * (i // 8))
        time.sleep(0.35)
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


_VM = 'local d = find(Ext.UI.GetRoot(), "CharacterLevelUp", 0).DataContext '


def _fill_passive_selectors(log, limit=24):
    """Passive choices (manoeuvres, and other "pick N passives" lists) through the view model: TogglePassive on the first enabled,
    unselected option of any selector short of its count. One toggle per call (several in one frame don't all land)."""
    n = 0
    for _ in range(limit):
        res, _ = _client(FIND + _VM + """
local det = d.ClassProgressionDetails
for _, key in ipairs({"SubPassiveSelectors", "NotSubPassiveSelectors"}) do
  local col = det[key]
  for i = 1, (col and #col or 0) do
    local sel = col[i]
    if sel.SelectedPassiveCount < sel.MaxSelectedPassiveCount then
      for j = 1, #sel.Passives do
        local it = sel.Passives[j]
        if it.Enabled and not it.Blocked and tonumber(it.Value) == 0 then
          d.TogglePassive:Execute(it)
          return {toggled = it.IconName}
        end
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
        time.sleep(0.35)
    if n:
        log.append(f"passives: {n} toggled through the view model")
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
    time.sleep(1.0)                   # read back in a later frame: the same frame still shows the old value
    res, _ = _client(FIND + _VM + "return tostring(d.SelectedSubClass and d.SelectedSubClass.IDString)")
    return (str(res).lower() == name.lower()), f"subclass now {res}"


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


def levelup_auto(finish=True, add_class=None, subclass=None):
    """Level the host up completely: open the screen, (add_class: take the level in that class instead - a multiclass, or another
    level of a second class), fill every pending checklist row (spells, cantrips, rituals, savant, feat / ability improvement),
    then accept and wait for the level to apply. Validates as it goes: each row's marker must clear, IsLevelUpComplete must be
    true before Accept, the host level must rise by exactly one (and with add_class, that class's level by one). Returns {ok, log,
    level_before, level_after, classes_before, classes_after, open_s, choices_s, total_s, error}. subclass: on a level that offers a
    subclass choice, take this one (its IDString, e.g. "BattleMaster"; verified on the character afterwards) - otherwise the game's
    default (the first in its list) is kept."""
    out = {"ok": False, "log": []}
    log = out["log"]
    t0 = time.time()
    out["level_before"] = host_level()
    if out["level_before"] is None:
        out["error"] = "no host level from the Script Extender (is a game loaded?)"
        return out
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
                time.sleep(0.6)
            time.sleep(0.9)
            log.append(f"levelled owned class {add_class} (carousel position {steps + 1} of {len(owned)})")
        else:
            _rclick(*ADD_CLASS_BUTTON)
            time.sleep(1.2)
            _rclick(*CLASS_TILES[add_class])
            time.sleep(1.5)           # the class's first-level picks are pre-filled as it switches
            log.append(f"added class {add_class}")
    if subclass:
        ok, msg = _set_subclass(subclass)
        log.append(f"subclass {subclass}: {msg}")
        if not ok:
            out["error"] = f"couldn't choose subclass {subclass}: {msg}"
            return out
    _fill_passive_selectors(log)
    stuck = []
    rows = stable_pending_rows()
    for _ in range(14):
        rows = [y for y in rows if all(abs(y - z) > ROW_TOL for z in stuck)]
        if not rows:
            break
        if not _fill_row(rows[0], log):
            stuck.append(rows[0])
            if len(stuck) > 2:
                break
        rows = pending_rows() or []
    out["choices_s"] = round(time.time() - t0 - out["open_s"], 1)
    if not _state().get("complete"):
        out["error"] = "choices still pending after the driver ran: " + "; ".join(log[-3:] or ["no pending rows were found"])
        return out
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
        time.sleep(0.5)
        lv = host_level()
        if lv is not None and lv > before:
            return True, f"level {before} -> {lv} after {time.time() - t0:.1f}s"
    return False, f"accepted, but the level hadn't risen after {wait:.0f}s"


def load_save(index=0, timeout=90.0):
    """Load a save from the pause menu of the RUNNING game (no restart): Esc until the pause menu (GameMenu) is confirmed open,
    Load Game, row `index` of the list (0 = first row = the game's newest), Load Game. Waits until a host exists in the new
    session, clearing message boxes with Enter. Returns (seconds, host level), or (None, reason)."""
    t0 = time.time()
    for _ in range(4):
        names = screen()
        if names is None:
            return None, "the Script Extender didn't answer, so the menu state is unknown (nothing was clicked)"
        if "GameMenu" in names:
            break
        send_key(0x01, hold_ms=100)   # closes whatever is open (level-up, sheet), then opens the pause menu
        time.sleep(1.0)
    else:
        return None, "the pause menu didn't open (nothing was clicked)"
    _rclick(960, 568)                 # Load Game
    time.sleep(3.0)                   # the list fills in after a spinner
    _rclick(320, 210 + 34 * index)
    time.sleep(0.4)
    _rclick(1068, 1005)               # Load Game button
    time.sleep(6.0)
    while time.time() - t0 < timeout:
        send_key(0x1C, hold_ms=60)    # the [ForceUpdate] box appears as the save starts loading
        time.sleep(1.5)
        lv = host_level()
        if lv is not None:
            return round(time.time() - t0, 1), lv
    return None, f"no host after {timeout:.0f}s"


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
