# Driving the level-up screen from the MCP (research, 2026-10-03)

Goal: `bg3_level_up` grants the XP, but the level-up itself is clicks in the game UI, so tests of features gained at 13+ need a
person. Findings from a timeboxed probe (test character: Wizard 3 with exactly the XP for level 4; Script Extender client console):

What works / is known
- `bg3_level_up` makes the entity level-up-ready: `Experience.TotalExperience == NextLevelExperience`, and the character
  carries `CanLevelUp` (empty tag component). Components `LevelUp` and `CCLevelUp` hold the *history* of level-ups
  (class, subclass, spells/passives/abilities per level) - large; never `Ext.Types.Serialize` them wholesale.
- Posted keys (`gameui.press_key`, PostMessage) reach menus and message boxes but NOT gameplay hotkeys: `C` (character sheet)
  did nothing. Real hotkeys would need focus + SendInput.
- Noesis view models can be enumerated: `widget.DataContext:GetAllProperties()` (ui::DCWidget; also GetProperty /
  SetProperty / DirectProperties / DependencyProperties). The `PlayerPortraits` data context exposes 152 properties/commands,
  among them `StartLevelUp`, `OpenLearnSpellsCommand`, `QuickSaveCommand`, `OpenLoadGameDialog`, `ShortRest`, `EndTurn`,
  `ToggleTurnBasedMode`. A command is `Noesis::BaseCommand`; `cmd:Execute(x)` needs a *light C++ object* (a string or number
  errors: "expected a light C++ object").
- `dc.StartLevelUp:Execute(nil)` and `Execute(dc.CurrentPlayer)` are accepted but opened nothing visible within 4 s (no new
  widget under ContentRoot, game state stays `Running`).
- The hotbar has a `LevelUpHolder` element, `Collapsed` even when level-up-ready (a notification slot, not the entry point).
- Message boxes: `Dialog_box.DataContext` has `UUIDProperty`, `Actions[i].Name` (a localization handle) - e.g. the Game Over box
  is `GameOverMsgID` with "Main Menu" / "Load Game" (no cancel); other boxes show the placeholder text "[ForceUpdate]".

Leads, in the order I'd try them
1. Find what the portrait/character-sheet level-up button binds to: walk `PlayerPortraits` children's `DataContext`s and look
   for a per-character view model with a `LevelUp...` command whose parameter is that character's view model (not the
   portraits root).
2. Open the character sheet through a view-model command instead of the hotkey, then look for its level-up button.
3. Once a level-up widget exists, repeat the dialog trick: enumerate its data context, execute the accept/confirm commands, and
   drive the choices (class, subclass, spells, feat) the way `bg3_test_plan` already lists them.
4. Otherwise: SendInput with the window focused (focus_game.ps1 exists) for the hotkeys.

Safety when experimenting: a level-up is in-session only, but an autosave would make Continue load it - check `bg3_saves`
afterwards (none appeared in this probe) and restart from the test save.
