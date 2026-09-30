# In-game testing (any mod)

bg3-data-mcp can drive repeatable in-game tests for any mod layer. You keep playing normally (level-ups,
casting from the hotbar); the tools do the setup, recording, checking and cleanup.

## Why a player casts
A scripted cast (`Osi.UseSpell`) applies the spell's effects but **never pays its costs** (verified
2026-09-30, even for a class-learned spell), and a spell added by script (`Osi.AddSpell`) isn't part of the
class. So:
- `mode = "player"` (default): you cast from the class spell bar. The only run that verifies slots and
  resources, class ownership and turn flow.
- `mode = "script"`: the tool casts. Effects only; resource checks report SKIP. Every verdict says which
  kind of run it was.

## Loop per level
1. `bg3_level_up` grants exactly enough XP (from the active layers' `XPData.txt`) for the next level.
2. You level up in the UI, so every feature and spell comes from the class progression.
3. `bg3_level_check` compares the host with its class and subclass progressions up to its level:
   `PassivesAdded` present, `PassivesRemoved` gone, `AddSpells` lists learned (with their source),
   `ActionResource` boosts, plus this level's choices.
4. For each case: `bg3_test_stage <id>` sets it up, you cast, then `bg3_test_verify` checks it and cleans up.
5. Save only after verify/cleanup. Staged spawns and test boosts must never end up in a save.

`bg3_test_script` writes the human reading script (`<mod>/docs/test-scripts/<Class>[_L<n>].md`).
`bg3_game_restart(deploy_layer)` kills the game, runs the layer's `deploy` command, relaunches through
Steam with `-continueGame` (the newest save) and waits until a character is loaded.

## Where tests live
`<mod path>/tests/bg3/*.toml`, or set `"tests": "<dir>"` on the mod in `layers.json`. Add
`"deploy": "<shell command>"` (run in the mod folder) to use `bg3_game_restart(deploy_layer=...)`.

## Case format (TOML)
```toml
[suite]
name = "Wizard"
class = "Wizard"             # default for every case in the file (also: subclass, mode)

[[case]]
id = "wiz-01-magic-missile"  # unique within the layer
level = 1                    # class level the case is written for (stage blocks below it)
title = "Magic Missile hits a hostile wolf and spends a 1st-level slot"
spell = "Projectile_MagicMissile"
target = "A"                 # "host" or a spawn alias
mode = "player"              # or "script"
prep = "..."                 # optional: shown as "Before casting: ..."
instructions = "..."         # optional: extra step after the cast step
notes = "..."                # optional: shown at the end of the case
combat = true                # default: true when any spawn is hostile
initiative = "host_first"    # default in combat: temporary Initiative(50) boost on the host
safety = true                # default: kill spawns + heal if a party member drops below safety_floor
safety_floor = 35            # percent HP
spawn = [{ as = "A", template = "wolf", faction = "hostile", hp = 40, distance = 8 }]
setup = [{ target = "host", hp = 2 }, { target = "host", status = "FRIGHTENED", turns = 10 },
         { target = "A", boost = "Resistance(Fire,Resistant)" }, { target = "host", max_hp = 20 }]
expect = [
  { acted_first = true },                                   # first recorded turn was the host's
  { cast = true },                                          # the host cast `spell` (or cast = "OtherSpell")
  { target = "A", hp_change = [-15, -6], damage_type = "Force" },
  { target = "A", dead = true },
  { target = "host", hp = "full" },                         # or an exact number
  { target = "host", status_applied = ["MAGE_ARMOR"] },     # event seen during the run
  { target = "host", status_removed = ["FRIGHTENED"] },     # present before, gone after
  { target = "host", status_present = ["X"], status_absent = ["Y"] },
  { resource = "SpellSlot", level = 1, change = -1 },       # host by default; level 0 for non-slot resources
]
```
Templates: `wolf`, `boar`, `bear`, `skeleton`, `zombie`, or any root-template GUID. Factions: `hostile`,
`friendly`, `neutral`, or a faction GUID. `bg3_test_list` validates every case against the index
(unknown spells, statuses, aliases, keys).

## How it works
The harness (`bg3data/lua/harness.lua`) is written as a loose file under `<game>/Data/Public/BG3DataTest/`
and loaded through the SE console into the global `BG3T`. It re-installs itself after a Lua reset or a
version change. It tracks everything it creates (spawns, boosts tagged `BG3Test`, statuses) so cleanup is
complete, records Osiris events (StatusApplied/Removed, CastedSpell, UsingSpellOnTarget, AttackedBy,
Died, TurnStarted, CombatStarted/Ended, LeveledUp) for tracked characters, and runs the safety watch on
HitpointsChanged. Staging state is kept in the cache (`test_state.json`), so it survives an MCP restart.

## Known limits
- The Nautiloid tutorial (`TUT_SUMMON_BLOCK`) blocks summons: test summon spells after the crash site.
- The engine can't require a spell's second target to be within a distance of the first. Check that by eye.
- `Ext.StaticData` reads occasionally come back empty for a tick; host snapshots retry.
