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
- `levelup_open` clicks the LEVEL UP bar, waits ~2.5 s, then presses Enter once a second (4x): the intro ignores Enter until it has
  faded in, so earlier presses are swallowed. Open to interface is ~9 s and verified (L7 and L8 level-ups, 2026-10-03).
- Click targets: the checklist rows on the left are narrow (click near their icon/label, ~x=35 of the half-size shot), and the
  "+" tiles on the overview page are NOT clickable - open a choice by its checklist row, pick icons, then Accept.
- `bg3_click(points=[[x,y],...], screenshot=True)` batches several clicks and returns a fresh screenshot in one call.
- Remaining cost: each `bg3_se_eval` spawns PowerShell for console injection (~0.6 s after warm-up).
