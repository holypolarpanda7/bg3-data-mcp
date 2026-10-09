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
NEW_GAME = (280 / 960, 224 / 540)          # main menu button; then the difficulty page's Start Game
START_GAME = (532 / 960, 502 / 540)
DONT_RESET = (537 / 960, 303 / 540)


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


def launch_menu(deploy_layer=None, timeout=300, log=print):
    """A fresh game process at the main menu with every mod of modsettings.lsx loaded (the new-game path: unlike Continue, the
    mod list isn't taken from a save, and coming back to the menu from a loaded game drops the mods - seen 2026-10-08).
    Quits the game, optionally deploys `deploy_layer`, launches without -continueGame, and handles the splash screen and the
    no-mods safe mode (clean quit + relaunch) like bg3_game_restart. Returns (ok, message)."""
    from . import platform, testing
    msgs = []
    r = testing._restart(deploy_layer, launch=False)
    msgs.append(r)
    if deploy_layer and "failed" in r:
        return False, r
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
        gameui.click_frac(*NEW_GAME)
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
        if "CharacterCreation" not in names:
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
    for key, lst, setter in (("race", "SelectableRaces", "SelectedRace"), ("subrace", "SelectableSubRaces", "SelectedSubRace"),
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


def finish(timeout=180):
    """Proceed x3 (name prompt, dream guardian, Venture Forth), then Esc through the cutscenes until the host is in the world."""
    for _ in range(3):
        _answer_dialog()
        gameui.click_frac(*PROCEED)
        timing.wait(2.5)
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


def start(spec, log=print, fresh=True, deploy_layer=None):
    """A new game with `spec` -> in the world. Returns (ok, message). fresh=True (default) launches a new game process first:
    the mods only load into a new game when it is started from a fresh launch's menu, not after coming back from a loaded game."""
    if fresh:
        ok, msg = launch_menu(deploy_layer)
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
    ok, msg = finish()
    log(msg)
    if not ok:
        return False, msg
    if spec.get("save_as"):
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
    then its checks on the host. Returns a report. [[newgame]] keys: id, title, race, subrace, class, subclass, background,
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
    report = []
    for i, c in enumerate(cases):
        log(f"== {c['id']}: {c.get('title', '')}")
        ok, msg = start({k: c[k] for k in ("race", "subrace", "class", "subclass", "background") if k in c}, log,
                        fresh=True, deploy_layer=layer if i == 0 else None)
        if not ok:
            report.append(f"FAIL {c['id']}: {msg}")
            continue
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
        report.append(("PASS " if not fails else "FAIL ") + c["id"] + ("" if not fails else ": " + "; ".join(fails)))
    return "\n".join(report)
