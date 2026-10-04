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
