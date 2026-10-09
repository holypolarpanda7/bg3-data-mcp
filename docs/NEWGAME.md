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

## Not done yet
- Ability point / skill / spell / feat choices (the game's pre-fills are used), name entry, multiplayer origins, saving a start save.
- A generator of cases from the data (one per background / class / subclass): `bg3_new_game_options` is the input for it.
