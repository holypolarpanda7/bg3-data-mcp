# Levelling a test character through the MCP

Features gained at class levels 13-20 can only be tested by a character that has them. These tools take a character up a level
without clicking in the game yourself. They are opt-in helpers for Windows (WSL works); they type into the game, so use them
while nobody is typing.

## The loop (one level)
1. `bg3_level_up(levels=1)` - grants exactly the XP; wait ~3 s (a LEVEL UP bar then shows on the character sheet).
2. `bg3_levelup("open")` - presses the character sheet key (scan code `0x17` = I; pass `sheet_scan` if you remapped it), then clicks
   the LEVEL UP bar. It returns `{sheet_open, levelup_open, complete}`.
3. `bg3_screenshot` - look at the page. The checklist on the left lists what is pending (red `!`); the page in the middle shows
   the choices.
4. `bg3_click(x, y)` - click what you see (x, y are pixels of the screenshot; they are sent as fractions of the window, so they
   hold at any resolution of the same aspect ratio): a checklist row to switch page, then the tiles/feat/ability buttons.
   Repeat 3-4 until `bg3_levelup("state")` says `complete: true`.
5. `bg3_levelup("finish")` - accepts through the screen's own `FinishLevelUp` command (no Accept click).
6. `bg3_level_check` verifies what the level granted.

Verified 2026-10-03: Wizard 3 -> 6 (cantrip, spells, ritual spell, Savant spell, feat with ability points) with these tools.

## What is general and what isn't
- General: the tools themselves; the level-up screen exists for every player; completion (`IsLevelUpComplete`) and acceptance
  (`FinishLevelUp`) come from the screen's view model, not from pixels or text, so they don't depend on language or resolution.
- Layout-dependent: the LEVEL UP bar position (a 16:9 fraction) and the choices you click. Ultrawide or heavily scaled UIs may
  need a different bar position; the loop's screenshot step is how you find out.
- Key bindings differ per profile (the sheet key was I here, not C): `sheet_scan` is a parameter.
- Windows only (SendInput, PowerShell). Not for controllers. A game patch that redesigns the screen can break the clicks.
- Naming a spell: the spell entries expose only `Selected`/`NotAvailable`, so choices are made by looking and clicking, not by
  spell name. Subclass-granted "always prepared" spells need no choice.

## Speed (2026-10-03)
- Input goes through one long-lived helper (`bg3data/ps/inputd.ps1`, started on first use by `gameui._InputDaemon`): a click
  is ~0.17 s, a screenshot ~0.22 s, instead of ~1.7 s per script launch. The old per-call scripts remain as fallbacks.
- `levelup_open` clicks the LEVEL UP bar, then watches screen brightness (`lum` command of the helper: dark intro ~0-23, interface
  ~180) and returns the moment the interface shows. Correction: Enter/Space/click do NOT reliably skip the intro earlier - timelines
  show it ending by itself 6-9 s after the click whatever is pressed (Enter is still sent every 0.5 s, harmless).
- Click targets: the checklist rows on the left are narrow (click near their icon/label, ~x=35 of the half-size shot), and the
  "+" tiles on the overview page are NOT clickable - open a choice by its checklist row, pick icons, then Accept.
- `bg3_click(points=[[x,y],...], screenshot=True)` batches several clicks and returns a fresh screenshot in one call.
- Remaining cost: each `bg3_se_eval` spawns PowerShell for console injection (~0.6 s after warm-up).

## Benchmark (2026-10-03, Wizard 2 -> 5 from the "L2 Base" save, loaded from the pause menu without a restart)
- Each level: open to interface 11.7-13 s; choices by one batched `bg3_click(points=[...])` (4-7 clicks, ~1-2 s); Accept then ~6-8 s
  of black screen while it applies. L5 ran with zero screenshots (rows and icon positions repeat): click batch -> `state`
  complete -> `finish`. Layout facts (1920x1080 px): checklist rows x=70, y=166/210/254/(...) down the list; picker icons
  first row y=476 (x=324, 370), Savant/ritual pickers y=454-582; feat page: list x~394, then ability "+" at (1080,316),
  passive checkbox (870,452).
- `bg3_level_check` passes at each level (2 recurring WARNs: ArcaneWard and SpellSlot maxes read higher than the progression
  - probably the Origin feat/Alert and cumulative XP, not investigated).

## Round 2 (2026-10-03)
- `bg3_levelup finish` now blocks until the level has really been applied (server `Osi.GetLevel` rises; ~1-4 s) instead of returning
  during the black screen, so `bg3_level_check` right after it is safe.
- `bg3_load_save(index)` loads a save from the pause menu of the running game (no restart): ~34 s, clears [ForceUpdate].
- Measured, Wizard 2 -> 5 from the L2 save: L3 open 13.5 s + choices 1.9 s + finish 3.6 s = 19 s; L4 open 10.1 + choices 3.5 +
  finish 4.1 = 17.7 s (feat included); L5 choices+finish 7.7 s. Open is the floor: ~3 s for the sheet + the 6-9 s intro.

## Round 3 (2026-10-03): `bg3_levelup auto`
`auto` opens the screen, fills every pending checklist row, accepts, and validates. Needs a `/mcp` reconnect to appear as an action.
- Rows still pending are found by the red "!" marker (helper command `redrows`, scans a 32 px strip; ring and glyph clusters
  merge into one row). The picker type is found by brightness at the three known icon origins (`lum`): grid (spells/cantrips),
  ritual, savant; no icons = a text list = feat. Icons are clicked until that row's marker clears, so the right NUMBER is
  picked without reading "0/2". Feats try a chain (Actor, Alert, Athlete, Charger) until the Feat row clears (a taken feat
  can't be picked again; the details panel fades in ~1 s after the click).
- Validation built in: every row's marker must clear; `IsLevelUpComplete` must be true before Accept; the host level must rise by exactly one
  (`Osi.GetLevel`); the server action then runs `level_check` and fails the result on any FAIL line. The result carries
  `open_s / choices_s / total_s` and a log of what each row did.
- `tests/levelup_selftest.py` (offline logic tests, `--live` runs one real level) .
- Live results, Wizard 2 -> 6 from the L2 save: L3 27.8 s, L4 (feat) 25.6 s, L5 24.8 s, L6 20.4 s, all level_check PASS; "no
  level-up ready" fails cleanly in ~12 s.
- Bugs this round found and fixed: an empty marker list was read as "still pending" (extra icon clicks); row tolerance 25 px matched the
  next row (now 14); repeated clicks at the same spot did nothing because the UI needs a real mouse move (the helper now
  nudges 4 px first - this also explains earlier "click did nothing" cases); `LevelUpStep` ("IntroComplete") exists in the view model
  but its intro value was not caught.
- Known gaps: subclass/race/multiclass/ability-score pages aren't handled (the driver reports the row it couldn't clear and
  stops); the ArcaneWard / SpellSlot WARNs in level_check read +INT mod and an extra level-1/2 slot - the check ignores ability
  modifiers, not investigated further.

## Review pass (2026-10-03)
Fixed after a code review of rounds 1-3:
- Enter is only pressed while the screen is dark (the intro). Before, it was pressed on a timer and could land on the sheet or on
  the interface itself - unverified whether Enter accepts a level-up with no choices left.
- A level with no choices no longer waits ~5.6 s: `stable_pending_rows` accepts three equal empty reads.
- Pages without icons only get the feat chain when the view model says `CanSelectFeat`; a subclass/race/ability page is reported
  ("not handled") instead of being clicked like a feat list.
- Picker type: thin probe bands at heights where only that layout has art (`ICON_PROBES`). A wider probe (tried in round 3's
  review) overlapped the ritual icons and read the ritual page as the spell grid - it passed by luck.
- Unknown state is never treated as "closed": `levelup_state` has `known`; with the Script Extender silent nothing is pressed
  (the sheet key is a toggle). `load_save` clicks nothing unless the pause menu (GameMenu) is confirmed open, and returns the
  loaded host level so a wrong row shows immediately.
- Resolution: every level-up coordinate is 1920x1080 reference pixels sent as fractions; `redrows` takes its strip as fractions
  and returns client height + y, scaled back. Holds at any 16:9 size (not a non-default UI scale). Only tested at 1920x1080.
- The input helper restarts itself when inputd.ps1 changes (a hot reload kept the old helper, whose `redrows` reply format
  differs).
- `finish` reports failure when the level can't be confirmed (before: "accepted" without waiting). `auto` sets `error` when
  level_check finds FAILs.

Tests (`tests/levelup_selftest.py`, 30 offline checks, no game needed): pure logic, the fade-in/empty checklist, Enter-only-when-dark,
`levelup_auto` against a simulated screen (exact click counts, taken-feat fallthrough, unknown page not clicked, no level-up
ready, level not rising, no-choice level, no host), helper restart. Mutation-checked: reverting each fixed bug makes its tests
fail. `--live` levels the running host once and also fails if any picker needed more than 2 clicks (= picker misread).

Live, Wizard 2 -> 7 from the L2 save (2026-10-03): 20.9 s, 22.4 s (feat), 17.7 s, 16.6 s, 19.3 s (ritual + grid + savant); every row
cleared in its exact count; level_check ALL PASS at 7.

## Open items closed (2026-10-03)
- **Enter does not accept a level-up.** With every choice made, Enter and then Space were pressed on the interface: the level stayed
  (checked by `Osi.GetLevel`). The dark-only Enter rule stays as a precaution.
- **Ability Score Improvement** is now first in the feat chain: +2 to the class's primary ability (`ClassDescription.PrimaryAbility`,
  read in game), spilling to Constitution and then the rest when capped at 20. Live: Wizard 8, INT 17 -> 19. Panel: "+" at
  (1080, 316 + 44 * ability index).
- **Multiclass**: `bg3_levelup(action="auto", add_class="Cleric")`. The button at the panel's top right (708,120) opens Add Class (4x3
  grid of tiles); the game PRE-FILLS a new class's first-level picks (Cleric: 3 cantrips, Divine Order, deity, prepared spells),
  so the row loop only mops up. Validation adds: that class's level rose by one. Live: Wizard 8 -> Wizard 8 / Cleric 1.
- **level_check WARNs were the check's bugs, not mod bugs** (traced through the game's `BoostsContainer`, which names each boost's
  cause): it ignored race/subrace progressions (High Elf L3 +1 1st-level slot, L5 +1 2nd-level slot), skipped resource boosts of
  passives the class itself grants (ArcaneWard_Resource), and couldn't read `IF(AbilityGreaterThan(...))` (the ward's +INT mod). Now:
  race tables are walked (passives, spells, resources), every host passive's boosts count, ability conditions are evaluated against
  the real scores, other conditions give a range. Resources are pooled over all classes (one shared pool), a later class uses
  its IsMulticlass level-1 node (as the game does), and a multiclassed caster's slots come from the multiclass spellcaster table
  at the combined caster level (verified: Wizard 8 / Cleric 1 = 4/3/3/3/1).
- Subclass pages: a 2024 subclass comes at class level 3, so a subclass page shows when a class reaches 3 without one (the test
  save's Wizard had Abjuration from creation). Pages the driver can't fill are reported by row ("not handled"); not seen live yet.

Tests: `tests/levelup_selftest.py` 35 offline checks (ASI incl. a capped primary, add_class success / wrong class / unknown
name), `tests/level_check_selftest.py` 13 (ability conditions, multiclass table, caster modifiers).
