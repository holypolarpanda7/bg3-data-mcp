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


## Input pass-through (probe 2, same day)
- `Ext.Input.InjectKeyDown/Up/Press(<SDLScanCode label>)` exists in the client context and works at the UI layer: ESCAPE opened the
  `GameMenu`. It does NOT reach gameplay hotkeys (raw device state). Hold the key across two calls (down, ~0.3 s, up).
- OS-level `SendInput` with hardware scan codes (`bg3data/ps/sendkey.ps1`, `bg3_press_key`) works with the game focused: Esc
  opens/closes the GameMenu. No install needed.
- `C` (scan 0x2E) did not open any character-sheet widget in this profile, by either route; the game was hovering a hotbar
  tooltip at the time (screenshot). Next: look at the input scheme / a screenshot-driven check of other keys (I), or click the
  level-up indicator on the portrait with a SendInput mouse click.
- `bg3_screenshot` (bg3data/ps/screenshot.ps1) captures the game window so the screen can be inspected with Read.


## RESULT: the level-up screen opens (probe 3, same day)
Working path, all through the MCP's own pieces:
1. `bg3_level_up` grants the XP; wait a few seconds (the UI needs a moment: a **LEVEL UP** bar appears on the character sheet and a
   "Level Up" label under the portrait).
2. Open the character sheet with `bg3_press_key(0x17)` - **I** (not C) opens it in this profile.
3. Click the bar: `bg3_click(660, 170)` (game pixels; the half-size screenshot shows it at ~(330, 85)). The widget
   `CharacterLevelUp` appears: "Level Up! <Class> Lv N", Health Increased, Class Features.
   (`StartLevelUp:Execute(...)` and the button's own `Command:Execute()` ran without opening it; a real click did.)
4. `CharacterLevelUp.DataContext` has 249 properties/commands: `FinishLevelUp`, `SelectSpell`, `DeselectSpell`, `SelectAbility`,
   `SelectAbilityBonus`, `SelectableFeats`, `CanSelectFeat`, `SelectableSubClasses`, `SelectedSubClass`, `SelectableMultiClasses`,
   `SelectFirstUnusedClass`, `ProgressionSpells`, `ClassPassives`... Next step: drive these (choose subclass / spells / feat, then
   FinishLevelUp) from the plan `bg3_test_plan` already writes, then verify with `bg3_level_check`.
- Use `bg3_screenshot` + Read after each step to see the page; coordinates are client pixels.
- The level-up is in-session only; restart reloads the save. Check `bg3_saves` for stray autosaves.


## RESULT 2: a full level-up through the MCP (same day)
Wizard 3 -> 6 completed with `bg3_level_up` + I key + LEVEL UP bar click + checklist clicks + `FinishLevelUp`. Key facts:
`CharacterLevelUp.DataContext.IsLevelUpComplete` (bool) flips to true when the last choice is made; `FinishLevelUp:Execute(nil)`
accepts without clicking Accept; `SelectSpell:Execute(item)` on `ProgressionSpells` items does nothing (they are slot placeholders);
the checklist rows have uneven spacing, so choices are clicked by looking at a screenshot. Packaged as `bg3_levelup`
(see docs/LEVELUP.md).
