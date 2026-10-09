"""A real new game through the game's own character creation (2026-10-08), driven from the Script Extender client.

Found 2026-10-08 (see docs/NEWGAME.md): the main menu's view model has StartCharacterCreationCommand, the CharacterCreation widget's
data context lists the selectable races / classes / backgrounds and takes `dc.SelectedRace = item` (same for class, background), the
game pre-fills every other choice (abilities, skills, cantrips, spells), and three Proceed clicks finish it (name "Tav", the dream
guardian, "Venture Forth"). Cutscenes are skipped with Esc.

  start(spec)        main menu -> character creation -> the spec applied -> finished -> in the world, Esc-skipping every cinematic
  spec keys          race, subrace, class, subclass, background (an IDString, a name or a Guid), save_as
"""
import time

from . import gameui, se, timing

FIND = gameui.FIND
CC = 'find(Ext.UI.GetRoot(), "CharacterCreation", 0)'
# click targets as fractions of the window (measured on a 960x540 screenshot)
PROCEED = (535 / 960, 500 / 540)
TAMPER_ACCEPT = (480 / 960, 292 / 540)
START_GAME = (532 / 960, 502 / 540)        # the difficulty page's Start Game button (that page's layout is fixed)
DONT_RESET = (537 / 960, 303 / 540)
NAME_FIELD = (478 / 960, 444 / 540)
SCAN = {**dict(zip('qwertyuiop', range(0x10, 0x1A))), **dict(zip('asdfghjkl', range(0x1E, 0x27))),
        **dict(zip('zxcvbnm', range(0x2C, 0x33))), **dict(zip('1234567890', range(0x02, 0x0C))), ' ': 0x39}


def _names():
    return gameui.screen() or []


def _wait(pred, timeout, step=1.0):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        timing.wait(step)
    return None


def _client(code, timeout=15):
    res, raw = gameui._client(FIND + code, timeout=timeout)
    return res, raw


def _server(code, timeout=15):
    try:
        r = se.eval_lua(code, "server", timeout=timeout)
    except (RuntimeError, TimeoutError) as e:
        return None
    return r["result"] if r.get("ok") else None


def in_world():
    """The host exists in a region (a new game's first cutscene has none yet)."""
    r = _server("local h = Osi.GetHostCharacter() return h and Osi.GetRegion(h) or false")
    return bool(r) and not str(r).startswith("SYS_CC")    # SYS_CC_* is the creation room itself


def launch_menu(deploy_layer=None, timeout=300, log=print, isolate_layer=None):
    """A fresh game process at the main menu with every mod of modsettings.lsx loaded (the new-game path: unlike Continue, the
    mod list isn't taken from a save, and coming back to the menu from a loaded game drops the mods - seen 2026-10-08).
    Quits the game, optionally deploys `deploy_layer`, launches without -continueGame, and handles the splash screen and the
    no-mods safe mode (clean quit + relaunch) like bg3_game_restart. isolate_layer: modsettings.lsx is rebuilt for that layer
    (deploy.isolate: its dependencies, test_mods and nothing else) before the launch. Returns (ok, message)."""
    from . import deploy, platform, testing
    msgs = []
    r = testing._restart(deploy_layer, launch=False)
    msgs.append(r)
    if deploy_layer and "failed" in r:
        return False, r
    if isolate_layer:
        msgs += deploy.isolate(isolate_layer)
        log(msgs[-1] if len(msgs) < 3 else " | ".join(msgs[-2:]))
    _, g = testing.game_cfg()
    args = [a for a in g["launch_args"] if a.lower() != "-continuegame"]

    def launch():
        testing.clear_mod_crash_check(g, msgs)
        if g["launcher"] == "steam" and g.get("steam_exe"):
            platform.launch_detached(g["steam_exe"], ["-applaunch", str(g["app_id"])] + args)
        else:
            platform.launch_detached(g["game_exe"], args)
    launch()
    t0 = time.time()
    relaunched = False
    testing._unstick_menu.splash = 0
    while time.time() - t0 < timeout:
        timing.wait(3)
        if time.time() - t0 < 20:
            continue
        note = testing._unstick_menu(msgs, relaunched, menu_only=True)
        if note == "menu":
            return True, f"main menu after {time.time() - t0:.0f}s"
        if note in ("relaunch", "relaunch_killed"):
            relaunched = note == "relaunch"
            launch()
            msgs.append("relaunched after a clean quit")
            t0 = time.time()
    return False, f"no main menu within {timeout}s: " + " | ".join(msgs[-4:])


def to_main_menu(timeout=150):
    """From the world (pause menu -> Main Menu -> confirm) or from the splash screen to the main menu."""
    names = _names()
    if "MainMenu" in names:
        return True
    if "HotBar" in names:
        if not gameui._pause_menu():
            return False
        _client('local gm = find(Ext.UI.GetRoot(), "GameMenu", 0) gm.DataContext.MainMenuCommand:Execute(nil) return true')
        if not _wait(lambda: gameui.dialog_info(), 10, 0.5):
            return False
        gameui.accept_dialog()
    end = time.time() + timeout
    while time.time() < end:
        names = _names()
        if "MainMenu" in names:
            return True
        if "SplashScreen" in names:
            gameui.send_key(0x1C)
        timing.wait(2.0)
    return False


def _cc_ready():
    r, _ = _client(f'local c = {CC} if not c then return false end local ok, n = pcall(function() return #c.DataContext.SelectableRaces end) return ok and n > 0')
    return bool(r)


def _menu_anchor():
    """(x, y) in 960x540 screenshot pixels of the main menu's highlighted (teal) Continue pill, or None. The menu's position changes with
    the loaded mods (Mod Configuration Menu adds a button and moves everything), so New Game is found relative to it."""
    import numpy as np
    from PIL import Image
    path = gameui.screenshot()
    if not path:
        return None
    a = np.asarray(Image.open(path).convert("RGB").resize((960, 540))).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mask = (g - r > 25) & (b - r > 35) & (b < 150)
    mask[:, :100] = False
    mask[:, 330:] = False
    mask[:120] = False
    ys, xs = np.nonzero(mask)
    if len(ys) < 200:
        return None
    return float(np.median(xs)), float(np.median(ys))


def click_new_game():
    """Click the main menu's New Game button (one pill below Continue; the first pill when there is no save to continue)."""
    anchor = _menu_anchor()
    if not anchor:
        return False
    has_saves, _ = _client('return find(Ext.UI.GetRoot(), "MainMenu", 0).DataContext.HasSaveGames == true')
    x, y = anchor[0], anchor[1] + (32 if has_saves is not False else 0)
    return gameui.click_frac(x / 960, y / 540)


def _answer_dialog():
    """Clear a box over the creation screen: the 2-action "Reset Tutorials?" gets Don't Reset, an acknowledge box its one action."""
    info = gameui.dialog_info()
    if not info:
        return False
    if info["actions"] == 2:
        gameui.click_frac(*DONT_RESET)      # executing the action alone leaves the box open (like dismiss_dialog's acknowledge boxes)
    else:
        gameui.dismiss_dialog()
    timing.wait(1.0)
    return True


def start_cc(timeout=240):
    """Main menu -> the character creation screen, answering the loading warnings and skipping the intro cinematic."""
    if not _cc_ready():
        if "MainMenu" not in _names():
            return False, "not at the main menu"
        # The real New Game button path (New Game -> difficulty -> Start Game). The view model's StartCharacterCreationCommand
        # starts a VANILLA creation: no mod is loaded in that session (verified 2026-10-08, Rune Carver absent).
        if not click_new_game():
            return False, "couldn't find the main menu's New Game button"
        timing.wait(4.0)
        gameui.click_frac(*START_GAME)
    end = time.time() + timeout
    seen_ready = 0
    while time.time() < end:
        names = _names()
        if "CharacterCreation" in names and _cc_ready():
            if _answer_dialog():
                continue
            if not seen_ready:                           # the tutorial question can come a moment after the screen
                seen_ready = time.time() + 6
            if time.time() < seen_ready:
                timing.wait(1.0)
                continue
            return True, "character creation is open"
        if "CharacterCreation" not in names and "MainMenu" not in names:     # Esc on a menu page would go Back
            gameui.click_frac(*TAMPER_ACCEPT)        # the modded-save "tampering" warning is not a message box
            gameui.send_key(0x01, 80)                 # Esc skips the intro cinematic (and does nothing while loading)
        timing.wait(1.5)
    return False, f"character creation didn't open in {timeout}s"


def _loca(handle):
    return f'Ext.Loca.GetTranslatedString({handle!r})'


def apply_spec(spec):
    """Select race / subrace / class / subclass / background through the view model. Returns the selections read back."""
    def pick(list_name, set_name, want, how):
        code = f"""
local dc = {CC}.DataContext
local l = dc.{list_name}
local want = ({want!r}):lower()
for i = 1, #l do
  local it = l[i]
  local ids = {{}}
  for _, p in ipairs({{"IDString", "Guid", "ClassIdentifier", "SubclassIDString"}}) do local ok, v = pcall(function() return it[p] end) if ok and v then ids[#ids + 1] = tostring(v):lower() end end
  local okn, nm = pcall(function() return Ext.Loca.GetTranslatedString(tostring(it.Name)) end)
  if okn and nm then ids[#ids + 1] = nm:lower() end
  for _, v in ipairs(ids) do
    if v == want then dc.{set_name} = it return {{ok = true, id = ids[1], name = nm}} end
  end
end
local hits = {{}}
for i = 1, #l do
  local it = l[i]
  local ids = {{}}
  for _, p in ipairs({{"IDString", "SubclassIDString"}}) do local ok, v = pcall(function() return it[p] end) if ok and v then ids[#ids + 1] = tostring(v):lower() end end
  local okn, nm = pcall(function() return Ext.Loca.GetTranslatedString(tostring(it.Name)) end)
  if okn and nm then ids[#ids + 1] = nm:lower() end
  for _, v in ipairs(ids) do if v:find(want, 1, true) then hits[#hits + 1] = i break end end
end
if #hits == 1 then dc.{set_name} = l[hits[1]] return {{ok = true, id = tostring(l[hits[1]].IDString), fuzzy = true}} end
local seen = {{}}
for i = 1, #l do local ok, nm = pcall(function() return Ext.Loca.GetTranslatedString(tostring(l[i].Name)) end) local okid, id = pcall(function() return l[i].IDString end) seen[#seen + 1] = tostring(okid and id or "") .. "/" .. tostring(ok and nm or "?") end
return {{ok = false, seen = seen}}"""
        r, raw = _client(code, timeout=30)
        return r if r is not None else {"ok": False, "error": str(raw)[:300]}
    out = {}
    for key, lst, setter in (("origin", "SelectableOrigins", "SelectedOrigin"), ("race", "SelectableRaces", "SelectedRace"), ("subrace", "SelectableSubRaces", "SelectedSubRace"),
                             ("class", "SelectableClasses", "SelectedClass"), ("background", "SelectableBackgrounds", "SelectedBackground")):
        if spec.get(key):
            timing.wait(1.0)                       # a race / class change refreshes the dependent lists
            out[key] = pick(lst, setter, spec[key], key)
            if not (out[key] or {}).get("ok"):
                return False, out
    if spec.get("subclass"):
        timing.wait(1.0)
        out["subclass"] = pick("SelectableSubClasses", "SelectedClass", spec["subclass"], "subclass")
        if not (out["subclass"] or {}).get("ok"):
            return False, out
    return True, out


def _lua_list(items):
    return "{" + ",".join(repr(str(x)) for x in items) + "}"


_ROWS = f"""
local cc = {CC}
local dc = cc.DataContext
local rows = {{}}
local function walk(n, d)
  if d > 30 then return end
  local ok, c = pcall(function() return n.DataContext end)
  if ok and c then local okp, v = pcall(function() return c.CanIncrease ~= nil and tonumber(tostring(c.BaseValue)) >= 8 end) if okp and v then rows[tostring(c.Ability)] = c end end
  local okc, cnt = pcall(function() return n.VisualChildrenCount end)
  if okc and cnt then for i = 1, cnt do walk(n:VisualChild(i), d + 1) end end
end
walk(cc, 0)
"""


def _abilities():
    """{ability: (base score, can increase, can decrease)} of the creation screen's ability rows (stale duplicate rows have base 0
    and are ignored: commanding them leaves the real ones untouched)."""
    r, _ = _client(_ROWS + 'local out = {} for k, c in pairs(rows) do out[k] = {tonumber(tostring(c.BaseValue)), c.CanIncrease == true, c.CanDecrease == true} end return out')
    return {k: tuple(v) for k, v in (r or {}).items()}


def _ability_step(name, cmd):
    _client(_ROWS + f'local c = rows["{name}"] if c then dc.{cmd}:Execute(c) end return true')


def set_abilities(want):
    """Point-buy to `want` ({"Strength": 15, ...} base scores before racial / background bonuses, 8..15, 27 points)."""
    for _ in range(60):
        cur = _abilities()
        todo = [(n, "IncreaseAbility" if cur[n][0] < w else "DecreaseAbility") for n, w in want.items()
                if n in cur and cur[n][0] != w and (cur[n][1] if cur[n][0] < w else cur[n][2])]
        if not todo:
            break
        # lower first: points are freed before they are spent
        todo.sort(key=lambda t: t[1] != "DecreaseAbility")
        _ability_step(*todo[0])
        timing.wait(0.25)
    cur = _abilities()
    return all(cur.get(n, (None,))[0] == int(w) for n, w in want.items()), {n: v[0] for n, v in cur.items()}


def _skill_groups():
    r, _ = _client(f"""
local dc = {CC}.DataContext
local out = {{}}
for _, g in ipairs({{"ClassSkills", "RaceSkills"}}) do
  local grp = dc[g]
  local max = tonumber(tostring(grp.MaxSelectedSkillCount)) or 0
  local sk = {{}}
  for i = 1, #grp.Skills do local it = grp.Skills[i] sk[#sk + 1] = {{tostring(it.Skill), it.Selected == true, it.Enabled == true}} end
  out[g] = {{max = max, selected = tonumber(tostring(grp.SelectedSkillCount)) or 0, skills = sk}}
end
return out""")
    return r or {}


def _toggle_skill(group, skill):
    _client(f"""
local dc = {CC}.DataContext
local grp = dc["{group}"]
for i = 1, #grp.Skills do if tostring(grp.Skills[i].Skill) == "{skill}" then dc.ToggleSkill:Execute(grp.Skills[i]) break end end
return true""")


def set_skills(wanted=()):
    """Skill proficiency groups (ClassSkills, RaceSkills): the wanted skills first (an unwanted, enabled pick makes room), then any
    enabled skill until each group is full. One toggle per step, state re-read each time (the view model lags a command)."""
    wanted = [w.lower() for w in wanted]
    for _ in range(24):
        groups = _skill_groups()
        act = None
        for g, d in groups.items():
            if d["max"] <= 0:
                continue
            skills = d["skills"]
            for name, sel, en in skills:
                if name.lower() in wanted and en and not sel:
                    if d["selected"] >= d["max"]:      # make room first
                        drop = next((n for n, s2, e2 in skills if s2 and e2 and n.lower() not in wanted), None)
                        act = (g, drop) if drop else None
                    else:
                        act = (g, name)
                    break
            if act:
                break
            if d["selected"] < d["max"]:
                nxt = next((n for n, s2, e2 in skills if e2 and not s2 and n.lower() not in wanted), None) or \
                    next((n for n, s2, e2 in skills if e2 and not s2), None)
                if nxt:
                    act = (g, nxt)
                    break
        if not act:
            break
        _toggle_skill(*act)
        timing.wait(0.3)
    groups = _skill_groups()
    return all(d["max"] <= 0 or d["selected"] >= d["max"] for d in groups.values()), \
        {g: [n for n, s2, _ in d["skills"] if s2] for g, d in groups.items()}


def _step_rows():
    """Centre y (960x540 screenshot pixels) of every row of the creation screen's left checklist: each row has a ring icon at x ~ 12
    whose top and bottom edges are bright and 10 px apart (the label layout varies with race / class / mods, so rows are detected,
    not hardcoded)."""
    import numpy as np
    from PIL import Image
    path = gameui.screenshot()
    if not path:
        return []
    a = np.asarray(Image.open(path).convert("RGB").resize((960, 540))).astype(int)
    bright = ((a[:, 4:22, :].sum(axis=2)) > 450).sum(axis=1) >= 3
    ys = [y for y in range(60 if False else 0, 500) if bright[y]]
    groups = []
    for y in ys:
        if groups and y - groups[-1][-1] <= 2:
            groups[-1].append(y)
        else:
            groups.append([y])
    tops = [int(np.mean(g)) for g in groups]
    rows, i = [], 0
    while i < len(tops):
        if i + 1 < len(tops) and 7 <= tops[i + 1] - tops[i] <= 13:      # a ring: top and bottom edge
            rows.append((tops[i] + tops[i + 1]) // 2)
            i += 2
        else:
            i += 1
    return rows


_SECTIONS = f"""
local cc = {CC}
local secs, vis = {{}}, {{}}
local function walk(n, d)
  if d > 40 then return end
  local ok, c = pcall(function() return n.DataContext end)
  if ok and c then
    local o, v = pcall(function() return c.AddedCount ~= nil and c.Available ~= nil end)
    if o and v then secs[tostring(c)] = c end
    local o2, v2 = pcall(function() return c.NotAvailable ~= nil and c.Spell ~= nil end)
    if o2 and v2 then local okv, iv = pcall(function() return n.IsVisible end) if okv and iv then vis[tostring(c)] = true end end
  end
  local okc, cnt = pcall(function() return n.VisualChildrenCount end)
  if okc and cnt then for i = 1, cnt do walk(n:VisualChild(i), d + 1) end end
end
walk(cc, 0)
local dc = cc.DataContext
local function nm(it) local ok, t = pcall(function() return Ext.Loca.GetTranslatedString(tostring(it.Spell.Name)) end) return ok and t or "?" end
"""


def _sections():
    """The picker sections in the creation screen's tree: [{cantrips, actions, sub, added, complete, visible (its tiles are on screen:
    the open step), selected: [names in order], available: [names in tile order, "*" selected, "!" unavailable]}]."""
    r, _ = _client(_SECTIONS + """
local out = {}
for _, s in pairs(secs) do
  local sel, av, shown = {}, {}, false
  for i = 1, #s.Additions do sel[#sel + 1] = nm(s.Additions[i]) end
  for i = 1, #s.Available do
    local it = s.Available[i]
    av[#av + 1] = nm(it) .. (it.Selected and "*" or "") .. (it.NotAvailable and "!" or "")
    if vis[tostring(it)] then shown = true end
  end
  out[#out + 1] = {cantrips = s.IsCantrips == true, actions = s.IsActions == true, sub = s.IsSubProgression == true,
                   added = tonumber(tostring(s.AddedCount)) or 0, selected = sel, available = av, complete = s.IsComplete == true, visible = shown}
end
return out""", timeout=30)
    return r or []


def _class_picker(kind):
    for x in _sections():
        if not x["sub"] and not x["actions"] and x["cantrips"] == (kind == "cantrips"):
            return x
    return None


def _open_picker(kind):
    """Click the left checklist rows top to bottom until the class's `kind` ("cantrips" / "spells") picker is the step on screen."""
    sec = _class_picker(kind)
    if sec and sec["visible"]:
        return True
    for y in _step_rows():
        gameui.click_frac(45 / 960, y / 540)
        timing.wait(0.8)
        sec = _class_picker(kind)
        if sec and sec["visible"]:
            return True
    return False


# The open picker's tiles on a 960x540 screenshot (measured 2026-10-08, cantrips and spells panels alike): the Selected row is centred on
# x = 240 at y = 195, the Available grid starts at (162, 238) with 8 columns 22.2 px and rows 22.4 px apart.
def _selected_xy(k, n):
    return (240 + 22.0 * (k - (n - 1) / 2)) / 960, 195 / 540


def _available_xy(i):
    return (162 + 22.2 * (i % 8)) / 960, (238 + 22.4 * (i // 8)) / 540


def set_spell_picks(kind, wanted):
    """Pick the class's `kind` ("cantrips" / "spells") by English display name ("Fire Bolt"): opens that step, then real clicks - a
    selected tile is clicked to drop it, an available tile to take it - because the view model's SelectSpell / DeselectSpell
    commands run without effect from Lua. State is re-read after every click. Returns (ok, details)."""
    want = [w.lower() for w in wanted]
    if not _open_picker(kind):
        return False, {"error": f"the {kind} step wasn't found in the checklist"}
    for _ in range(30):
        sec = _class_picker(kind)
        have = [n.lower() for n in sec["selected"]]
        missing = [w for w in want if w not in have]
        extra = [n for n in sec["selected"] if n.lower() not in want]
        if not missing:
            break
        offered = [a.rstrip("*!") for a in sec["available"]]
        target = next((m for m in missing if m in [o.lower() for o in offered]), None)
        if target is None:
            return False, {"selected": sec["selected"], "not offered": missing, "offered": sorted(offered)}
        if sec["complete"] and extra:           # full: drop an unwanted pick first
            k = sec["selected"].index(extra[0])
            gameui.click_frac(*_selected_xy(k, len(sec["selected"])))
        else:
            i = [o.lower() for o in offered].index(target)
            if sec["available"][i].endswith("!"):
                return False, {"error": f"{offered[i]} is not available to this character"}
            gameui.click_frac(*_available_xy(i))
        timing.wait(0.9)
    sec = _class_picker(kind)
    got = sec["selected"] if sec else []
    return all(w in [g.lower() for g in got] for w in want), {"selected": got}


def _checkbox_rows(n):
    """(x, [centre y of each of n checkboxes]) when the open step shows a list of n checkboxes, else (x, []): a column of small squares
    whose left edge is a thin bright line (dark on both sides) at a steady ~13 px; the x depends on the list (they are centred) and the
    detector misses a row now and then, so the rows are fitted to a lattice: the longest steady chain gives the step, every detected run
    on that lattice gives the extent, and the list matches when its extent is n rows (+-1). All coordinates are 960x540 screenshot pixels."""
    import numpy as np
    from PIL import Image
    path = gameui.screenshot()
    if not path:
        return 0, []
    a = np.asarray(Image.open(path).convert("RGB").resize((960, 540))).astype(int)
    r = a[..., 0]
    best = (0, 0, [], [])
    for x in range(150, 262):
        col = (r[:, x] > 55) & (r[:, x] < 200) & (r[:, x - 2] < 45) & (r[:, x + 2] < 45)
        runs, cur = [], []
        for y in range(95, 345):
            if col[y]:
                cur.append(y)
            elif cur:
                runs.append(cur)
                cur = []
        ys = [int(np.mean(g)) for g in runs if 2 <= len(g) <= 10]
        chain, chains = [], []
        for y in ys:
            if chain:
                gap = (y - chain[-1]) / 13.2
                k = round(gap)
                if not (1 <= k <= 3 and abs(gap - k) < 0.2):      # one to three missed rows are fine
                    chains.append(chain)
                    chain = []
            chain.append(y)
        chains.append(chain)
        top = max(chains, key=len)
        if len(top) > best[0]:
            best = (len(top), x, top, ys)
    cnt, x, chain, ys = best
    if cnt < 3:
        return x, []
    nrows = max(1, round((chain[-1] - chain[0]) / 13.15))
    step = (chain[-1] - chain[0]) / nrows
    ks = sorted({round((y - chain[0]) / step) for y in ys if abs((y - chain[0]) / step - round((y - chain[0]) / step)) < 0.2})
    span = ks[-1] - ks[0] + 1
    if abs(span - n) > 1:
        return x, []
    y0 = chain[0] + step * ks[0]
    return x, [int(round(y0 + step * k)) for k in range(n)]


_PICKERS = f"""
local cc = {CC}
local found = {{}}
local function walk(n, d)
  if d > 40 then return end
  local ok, c = pcall(function() return n.DataContext end)
  if ok and c then
    local o, v = pcall(function() return c.MaxSelectedPassiveCount ~= nil and c.Passives ~= nil end)
    if o and v then
      local okv, iv = pcall(function() return n.IsVisible end)
      local okt, t = pcall(function() return Ext.Loca.GetTranslatedString(tostring(c.Name)) end)
      local names = {{}}
      for i = 1, #c.Passives do
        local it = c.Passives[i]
        local okn, nmv = pcall(function() return Ext.Loca.GetTranslatedString(tostring(it.Name)) end)
        names[#names + 1] = {{(okn and nmv or tostring(it.Name)), tonumber(tostring(it.Value)) or 0, it.Enabled == true}}
      end
      found[#found + 1] = {{title = okt and t or "?", visible = okv and iv == true, max = tonumber(tostring(c.MaxSelectedPassiveCount)) or 0,
                            selected = tonumber(tostring(c.SelectedPassiveCount)) or 0, items = names}}
    end
  end
  local okc, cnt = pcall(function() return n.VisualChildrenCount end)
  if okc and cnt then for i = 1, cnt do walk(n:VisualChild(i), d + 1) end end
end
walk(cc, 0)
return found"""


def _pickers():
    """The passive pickers in the tree (race / class choices such as Versatile's origin feat, Skillful, weapon mastery): [{title,
    visible, max, selected, items: [[name, 0|1, enabled]]}]. Duplicates of the same picker are merged."""
    r, _ = _client(_PICKERS, timeout=30)
    out, seen = [], set()
    for p in r or []:
        key = (p["title"], p["visible"], tuple(i[0] for i in p["items"]))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _picker_by_title(title):
    for p in _pickers():
        if p["title"].lower() == title.lower():
            return p
    return None


def set_picker(title, choice):
    """Choose `choice` (a substring of the entry's name, e.g. "Lucky" in "Origin Feat: Lucky") in the passive picker called `title`
    ("Versatile", "Skillful", ...). The tree holds stale duplicates that all report visible, so the open step is recognised by its
    checkbox count on screen matching the picker's entry count: the checklist rows are clicked top to bottom until it does. Then the
    entry's checkbox is clicked and the picker's state re-read. Returns (ok, details)."""
    pk = _picker_by_title(title)
    if not pk:
        return False, {"error": f"no picker called {title!r}", "pickers": sorted({p['title'] for p in _pickers()})}
    names = [i[0] for i in pk["items"]]
    idx = next((k for k, n in enumerate(names) if choice.lower() in n.lower()), None)
    if idx is None:
        return False, {"error": f"{choice!r} not offered", "offered": names}
    if pk["items"][idx][1]:
        return True, {"selected": names[idx], "already": True}
    xcol, rows = _checkbox_rows(len(names))
    if not rows:
        for y in _step_rows():
            gameui.click_frac(45 / 960, y / 540)
            timing.wait(0.8)
            xcol, rows = _checkbox_rows(len(names))
            if rows:
                break
    if not rows:
        return False, {"error": f"no step showing a list of {len(names)} checkboxes found"}
    gameui.click_frac((xcol + 5) / 960, rows[idx] / 540)
    timing.wait(0.9)
    pk = _picker_by_title(title)
    ok = bool(pk and pk["items"][idx][1])
    return ok, {"selected": [i[0] for i in (pk or {"items": []})["items"] if i[1]]}


def character_complete():
    r, _ = _client(f'return {CC}.DataContext.IsCharacterCompleteExceptName == true')
    return bool(r)


def complete(spec, log=print):
    """After the selections: abilities / skills / cantrips / spells from the spec, then fill whatever the game still marks pending
    (a skill clash between class and background is the usual one) until the character is complete except for the name."""
    out = {}
    if spec.get("abilities"):
        out["abilities"] = set_abilities(spec["abilities"])
    for kind in ("cantrips", "spells"):
        if spec.get(kind):
            out[kind] = set_spell_picks(kind, spec[kind])
            if not out[kind][0]:
                return False, out
    for title, choice in (spec.get("picks") or {}).items():
        out["pick " + title] = set_picker(title, choice)
        if not out["pick " + title][0]:
            return False, out
    out["skills"] = set_skills(spec.get("skills") or ())
    if not character_complete() and not spec.get("abilities"):
        _client(f"{CC}.DataContext.UseRecommendedAbilities:Execute(nil) return true")   # a leftover unspent ability point
        timing.wait(1.0)
        out["skills"] = set_skills(spec.get("skills") or ())
    out["complete"] = character_complete()
    if not out["complete"]:
        r, _ = _client(f"local dc = {CC}.DataContext return {{unused = tostring(dc.UnusedAbilityPoints), classSkills = tostring(dc.ClassSkills.IsComplete)}}")
        out["pending"] = r      # e.g. unused ability points: a spec's abilities must spend all 27 points
    return out["complete"], out


def type_name(name):
    """Type into the name prompt: letters (capitals with a Shift chord), digits and spaces."""
    gameui.click_frac(*NAME_FIELD)
    timing.wait(0.4)
    for _ in range(24):
        gameui.send_key(0x0E, 60)
        timing.wait(0.04)
    for ch in name:
        if ch.lower() in SCAN:
            if ch.isupper():
                gameui._fast(f"chord 42 {SCAN[ch.lower()]}")      # Shift + letter
            else:
                gameui.send_key(SCAN[ch], 60)
            timing.wait(0.08)


def finish(timeout=180, name=None):
    """Proceed x3 (name prompt, dream guardian, Venture Forth), then Esc through the cutscenes until the host is in the world.
    name: typed into the prompt instead of the default "Tav"."""
    for i in range(3):
        _answer_dialog()
        gameui.click_frac(*PROCEED)
        timing.wait(2.5)
        if i == 0 and name:
            type_name(name)
    end = time.time() + timeout
    while time.time() < end:
        names = _names()
        if "CharacterCreation" not in names and in_world():
            if "GameMenu" in names:
                gameui.send_key(0x01, 80)
            else:
                return True, "in the world"
        elif "CharacterCreation" not in names:
            gameui.send_key(0x01, 80)                # skip the cinematic
        else:
            _answer_dialog()
            gameui.click_frac(*PROCEED)
        timing.wait(2.0)
    return False, f"not in the world after {timeout}s"


def host_summary():
    return _server("""
local h = Osi.GetHostCharacter() local e = Ext.Entity.Get(h)
local cls = {} for _, c in ipairs(e.Classes.Classes) do cls[#cls + 1] = {class = tostring(c.ClassUUID), sub = tostring(c.SubClassUUID), level = c.Level} end
return {host = h, level = Osi.GetLevel(h), region = Osi.GetRegion(h), race = tostring(e.Race and e.Race.Race), classes = cls, background = tostring(e.Background and e.Background.Background)}""")


def start(spec, log=print, fresh=True, deploy_layer=None, isolate_layer=None):
    """A new game with `spec` -> in the world. Returns (ok, message). fresh=True (default) launches a new game process first:
    the mods only load into a new game when it is started from a fresh launch's menu, not after coming back from a loaded game."""
    if fresh:
        ok, msg = launch_menu(deploy_layer, log=log, isolate_layer=isolate_layer)
        log(msg)
        if not ok:
            return False, msg
    elif not to_main_menu():
        return False, "couldn't reach the main menu"
    ok, msg = start_cc()
    log(msg)
    if not ok:
        return False, msg
    ok, sel = apply_spec(spec)
    log(f"selections: {sel}")
    if not ok:
        return False, f"selection failed: {sel}"
    ok, info = complete(spec, log)
    log(f"completion: {info}")
    if not ok:
        return False, f"the character isn't complete: {info}"
    ok, msg = finish(name=spec.get("name"))
    log(msg)
    if not ok:
        return False, msg
    if spec.get("save_as"):
        timing.wait(10.0)               # the opening sequence needs a moment before its save dialog loads
        saved, info = gameui.save_game(spec["save_as"])
        log(f"save: {info}")
    return True, str(host_summary())


def options(log=print):
    """What the creation screen offers with the mods now loaded: races, subraces (per race), classes, subclasses (per class)
    and backgrounds, as names (IDString/name). Opens creation from a fresh launch; the options change with the mod list."""
    ok, msg = launch_menu()
    if not ok:
        return {"error": msg}
    ok, msg = start_cc()
    if not ok:
        return {"error": msg}
    code = """
local dc = %s.DataContext
local function names(l) local t = {} for i = 1, #l do local ok, nm = pcall(function() return Ext.Loca.GetTranslatedString(tostring(l[i].Name)) end) local okid, id = pcall(function() return l[i].IDString end) t[#t + 1] = (okid and id and tostring(id) or tostring(l[i].Guid)) .. "/" .. tostring(ok and nm or "?") end return t end
local out = {races = names(dc.SelectableRaces), classes = names(dc.SelectableClasses), backgrounds = names(dc.SelectableBackgrounds), subraces = {}, subclasses = {}}
return out""" % CC
    res, _ = _client(code, timeout=30)
    res = res or {}
    for kind, lst, setter, sub, subl in (("races", "SelectableRaces", "SelectedRace", "subraces", "SelectableSubRaces"),
                                         ("classes", "SelectableClasses", "SelectedClass", "subclasses", "SelectableSubClasses")):
        res.setdefault(sub, {})
        res[sub] = {}
        for entry in res.get(kind, []):
            key = entry.split("/")[0]
            apply_spec({"race" if kind == "races" else "class": key})
            timing.wait(1.0)
            r, _ = _client(f"""
local dc = {CC}.DataContext
local l = dc.{subl}
local t = {{}}
for i = 1, #l do local ok, nm = pcall(function() return Ext.Loca.GetTranslatedString(tostring(l[i].Name)) end) local okid, id = pcall(function() return l[i].IDString end) t[#t + 1] = tostring(okid and id or "") .. "/" .. tostring(ok and nm or "?") end
return t""", timeout=20)
            if r:
                res[sub][key] = r
    return res


def run_cases(layer, only=None, log=print):
    """Run the layer's [[newgame]] cases (tests/bg3/*.toml): each is a fresh launch, a real new game with the case's choices,
    then its checks on the host. Returns a report. [[newgame]] keys: id, title, origin, race, subrace, class, subclass, background, abilities, skills, cantrips, spells, picks, name,
    checks = [{passive = "X"} | {spell = "X"} | {lua = "code returning true"}]."""
    import glob
    import os
    import tomllib
    from . import testing
    cases = []
    m = testing.mod_entry(layer)
    for f in sorted(glob.glob(os.path.join(m["path"], "tests", "bg3", "*.toml"))):
        with open(f, "rb") as fh:
            cases += tomllib.load(fh).get("newgame", [])
    cases = [c for c in cases if not only or c["id"] in only]
    if not cases:
        return f"no [[newgame]] cases for {layer}" + (f" matching {only}" if only else "")
    from . import deploy
    report = []
    try:
        for n, c in enumerate(cases):
            log(f"== {c['id']}: {c.get('title', '')}")
            report.append(_run_case(c, layer, n == 0, log))
    finally:
        for line in deploy.restore_isolation(layer):
            log(line)
    return "\n".join(report)


SPEC_KEYS = ("origin", "race", "subrace", "class", "subclass", "background", "abilities", "skills", "cantrips", "spells", "picks", "name")


def _run_case(c, layer, first, log):
    ok, msg = start({k: c[k] for k in SPEC_KEYS if k in c}, log, fresh=True, deploy_layer=layer if first else None,
                    isolate_layer=layer)
    if not ok:
        return f"FAIL {c['id']}: {msg}"
    fails = []
    for chk in c.get("checks", []):
        if "passive" in chk:
            r = _server(f"return Osi.HasPassive(Osi.GetHostCharacter(), {chk['passive']!r}) == 1")
            if r is not True:
                fails.append(f"passive {chk['passive']} missing")
        elif "spell" in chk:
            r = _server(f"local e = Ext.Entity.Get(Osi.GetHostCharacter()) for _, s in ipairs(e.SpellBook.Spells) do if tostring(s.Id.OriginatorPrototype) == {chk['spell']!r} then return true end end return false")
            if r is not True:
                fails.append(f"spell {chk['spell']} missing")
        elif "lua" in chk:
            r = _server(chk["lua"])
            if r is not True:
                fails.append(f"lua check returned {r!r}: {chk['lua'][:80]}")
    return ("PASS " if not fails else "FAIL ") + c["id"] + ("" if not fails else ": " + "; ".join(fails))


# ------------------------------------------------------------------ case generator
def _slug(text):
    import re
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _loca_names(mod_path):
    """handle -> English text from the mod's own Localization/English/*.xml."""
    import glob
    import os
    import re
    names = {}
    for f in glob.glob(os.path.join(mod_path, "Mods", "*", "Localization", "English", "*.xml")):
        for h, v in re.findall(r'<content contentuid="([^"]+)"[^>]*>(.*?)</content>', open(f, encoding="utf-8").read(), re.S):
            names[h] = re.sub(r"<[^>]+>", "", v).strip()
    return names


def _toml_case(c):
    lines = ["[[newgame]]"]
    for k in ("id", "title", "race", "subrace", "class", "subclass", "background"):
        if k in c:
            lines.append(f'{k} = "{c[k]}"')
    chk = ", ".join("{ " + " , ".join(f'{k} = "{v}"' for k, v in x.items()) + " }" for x in c["checks"])
    lines.append(f"checks = [{chk}]")
    return "\n".join(lines)


def generate(layer, kind="backgrounds", race="Human", cls="Fighter", write=False, log=print):
    """[[newgame]] cases from data. kind:
      backgrounds - one case per background the layer's own Public/*/Backgrounds/Backgrounds.lsx defines (no game needed): the
                    background is picked on a `race` `cls` character and every passive the background grants is checked;
      classes     - one case per class and per level-1 subclass the creation screen offers with the loaded mods (opens the game,
                    see options()): the host's class / subclass is checked against the ClassDescription names.
    Returns the TOML text; write=True stores it as <layer>/tests/bg3/newgame-<kind>.toml."""
    import glob
    import os
    import re
    from . import testing
    m = testing.mod_entry(layer)
    cases = []
    if kind == "backgrounds":
        names = _loca_names(m["path"])
        for f in glob.glob(os.path.join(m["path"], "Public", "*", "Backgrounds", "Backgrounds.lsx")):
            for blk in re.findall(r'<node id="Background">(.*?)</node>\s*(?=<node id="Background">|</children>)', open(f, encoding="utf-8").read(), re.S):
                dn = re.search(r'id="DisplayName"[^>]*handle="([^"]+)"', blk)
                passives = re.search(r'id="Passives"[^>]*value="([^"]*)"', blk)
                if not (dn and dn.group(1) in names):
                    continue
                name = names[dn.group(1)]
                cases.append({"id": "newgame-bg-" + _slug(name), "title": f"{name} background at character creation grants its passives",
                              "race": race, "class": cls, "background": name,
                              "checks": [{"passive": p} for p in (passives.group(1).split(";") if passives else []) if p]})
    elif kind == "classes":
        opts = options(log)
        if "error" in opts:
            return "error: " + opts["error"]
        for entry in opts.get("classes", []):
            cid, _, cname = entry.partition("/")
            cases.append({"id": "newgame-class-" + _slug(cid), "title": f"{cname} at character creation",
                          "race": race, "class": cid,
                          "checks": [{"lua": f"local e = Ext.Entity.Get(Osi.GetHostCharacter()) local c = Ext.StaticData.Get(e.Classes.Classes[1].ClassUUID, 'ClassDescription') return c ~= nil and c.Name == '{cid}'"}]})
            for sub in opts.get("subclasses", {}).get(cid, []):
                sid, _, sname = sub.partition("/")
                cases.append({"id": "newgame-sub-" + _slug(cid) + "-" + _slug(sid), "title": f"{cname}, {sname} at character creation",
                              "race": race, "class": cid, "subclass": sid,
                              "checks": [{"lua": f"local e = Ext.Entity.Get(Osi.GetHostCharacter()) local c = Ext.StaticData.Get(e.Classes.Classes[1].SubClassUUID, 'ClassDescription') return c ~= nil and c.Name == '{sid}'"}]})
    else:
        return f"unknown kind {kind!r} (backgrounds, classes)"
    text = ("# Generated by bg3data.newgame.generate(" + repr(layer) + ", " + repr(kind) + ") - regenerate after the data changes.\n\n"
            + "\n\n".join(_toml_case(c) for c in cases) + "\n")
    if write:
        out = os.path.join(m["path"], "tests", "bg3", f"newgame-{kind}.toml")
        open(out, "w", encoding="utf-8", newline="\n").write(text)
        log(f"wrote {out} ({len(cases)} cases)")
    return text
