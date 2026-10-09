# Real new-game tests (character creation driven end to end)

`bg3data/newgame.py`, tools `bg3_new_game`, `bg3_new_game_options`, `bg3_test_newgame`. Built and verified 2026-10-08.

## What it does
Quit the game -> (deploy the layer) -> launch a FRESH process without `-continueGame` -> main menu (splash Enter, no-mods safe-mode
relaunch handled by `testing._unstick_menu`) -> New Game -> Start Game -> Esc through the intro -> character creation ->
answer the tutorial box (Don't Reset) -> pick the spec on the creation screen -> Proceed x3 (name "Tav", dream guardian, Venture
Forth) -> Esc through the cutscenes until the host is in a real region (the opening nautiloid, TUT_Avernus_C, level 1).
About 3 minutes per run (about 50 s of that is the launch).

## [[newgame]] cases (tests/bg3/*.toml)
```toml
[[newgame]]
id = "newgame-rune-carver"
title = "..."
race = "Human"          # IDString, Guid or the localized name; subrace / subclass the same way
class = "Fighter"
background = "Rune Carver"
checks = [{ passive = "PHB2024_Background_RuneCarver" }, { spell = "Target_X" }, { lua = "return true" }]
```
`bg3_test_newgame("bigby")` deploys the layer first, then runs every case from its own fresh launch. The CREATION OPTIONS DEPEND ON THE
MODS LOADED: run `bg3_new_game_options` to see them; a name that matches nothing aborts the case and lists what was offered. A
substring that matches exactly one entry is accepted ("Life" -> LifeDomain).

## Facts found (all verified in game)
- Mods load into a new game ONLY when it is started from a fresh launch's main menu and through the real New Game -> Start Game
  buttons. The main menu view model's `StartCharacterCreationCommand` starts a vanilla session (no mods; Rune Carver absent).
  Coming back to the menu from a loaded game and starting a new game also had no mods, hence `launch_menu`.
- `modsettings.lsx` written by the game's Mod Manager has real MD5 / PublishHandle / Version64 per mod and no ModOrder node; the
  hand-written one (MD5 "") also produced a no-mods new game once. Not separated; the Mod Manager's "All" checkbox rewrites the
  file in the game's own form (it also drops mods the manager doesn't list, e.g. MCM - re-add it afterwards).
- Creation view model (`CharacterCreation` widget): `dc.SelectedRace / SelectedSubRace / SelectedClass / SelectedBackground = item`
  work; subclasses are items of `SelectableSubClasses` assigned to `SelectedClass`; the game pre-fills abilities, skills, cantrips
  and spells (a default pick is complete). Items have Guid/Name (loca handle) and for races/classes/subclasses an IDString.
- Executing a Dialog_box action leaves the box open; a real click answers it (Reset Tutorials -> Don't Reset).
- The "tampering/corruption" box at load is not a message box: click Accept at the box's position.
- The name stays "Tav": `CharacterName` is a LocaString (a Lua string can't be assigned).
- Saving during the opening sequence failed ("no new save named ..." after 40 s), so save_as may not work there.
- The tests run in the opening region; skipping its cutscenes is Esc.

## Choices beyond race / class / background (verified 2026-10-08)
- `abilities = { Strength = 8, Dexterity = 14, ... }`: base scores (before the +2/+1 bonuses), point buy, all 27 points must be spent or the
  case fails with the pending state. Done through the ability rows' Increase/DecreaseAbility commands (the visual tree also holds stale
  duplicate rows with base 0; they are ignored).
- `skills = ["Arcana", "Investigation"]`: the class (and race) skill groups, ToggleSkill one step at a time; the game's own pick is kept
  for everything not listed, and a class/background skill clash ("Choices Pending", the usual reason a default isn't complete) is
  filled automatically.
- `name = "ada lovelace"`: typed into the name prompt (letters, digits, spaces, lowercase: the key helper has no Shift). Default "Tav".
- `save_as`: works (the first attempt only failed because the save dialog and its preview load slowly right after a new game); the
  game also writes its own autosave "A Nautiloid is Full" under the character's name.

## Generator
`bg3_new_game_generate(layer, kind)` / `newgame.generate`: `backgrounds` reads the layer's own Backgrounds.lsx + English loca (no game) and
checks every passive a background grants; `classes` opens the creation screen and makes one case per class and per level-1 subclass
the loaded mods offer (with dnd55e loaded Cleric etc. have none: it moves subclasses to level 3, `HasSubclasses` is false).

## Mod-list state (bg3_mod_state, deploy.isolate)
Tests load only what they need: `deploy.isolate(layer, extra=())` REBUILDS modsettings.lsx for a test - the base modules, the layer's
meta dependencies, its layers.json `test_mods` (before the layer: a mod that replaces another's content loads after it) and the layer, each
entry in the game's own form with the real MD5 of the pak (looked up by Folder, then by Name: Vortex names MCM's pak by its title). Entries
come from `mods_registry.json` (every entry the game or a manager ever wrote, remembered on each isolate) so a mod the game dropped can
still be put back. The original is backed up once and `restore_isolation` puts it back; `bg3_new_game`, `bg3_test_newgame` and the gate restore
in a finally block, `bg3_mod_state restore` fixes an interrupted run. New-game launches isolate the layer by default.

## Not done
- Picking specific cantrips / spells / a feat: the game's pre-fills are used. The creation screen builds each picker's tiles only while its
  step is open and the same spell appears in several pickers (class, race), so selecting by name changed nothing in a probe; it needs
  step navigation (clicking the left checklist) and per-picker identification.
- Capital letters in names, multiplayer origins, hirelings.
