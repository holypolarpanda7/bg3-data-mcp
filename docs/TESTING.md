# In-game testing (any mod)

bg3-data-mcp can drive repeatable in-game tests for any mod layer. You keep playing normally (level-ups,
casting from the hotbar); the tools do the setup, recording, checking and cleanup.

## Modes: how much is automated
A scripted cast (`Osi.UseSpell`) applies the spell's effects but **never pays its costs**, in or out of
combat, even for a class-learned spell (verified 2026-09-30: slot and Action Point unchanged). So:
- `mode = "auto"` (default): the tool stages a real encounter (combat, host first in initiative), casts by
  script and checks effects from recorded events. Resource expectations are checked **against the spell's
  UseCosts as the game loaded them** (`[data]` rows: right resource, level and amount, and the host has
  it). This catches the data bugs mods make; the engine charging it is vanilla behaviour.
  `bg3_test_run` / `bg3_test_run_level` do stage + cast + verify + cleanup in one call.
- `mode = "player"`: stage, you cast from the class spell bar, `bg3_test_verify`. Measures the real
  resource change; use it as a spot check (e.g. once per resource type).
- `mode = "script"`: effects only, no class-ownership requirement.
The spell must be learned through the class (SpellBook source is not `Osiris`) for auto and player cases.
Every verdict states which kind of run it was.

## Loop per level
1. `bg3_level_up` grants exactly enough XP (from the active layers' `XPData.txt`) for the next level.
2. You level up in the UI, so every feature and spell comes from the class progression.
3. `bg3_level_check` compares the host with its class and subclass progressions up to its level:
   `PassivesAdded` present, `PassivesRemoved` gone, `AddSpells` lists learned (with their source),
   `ActionResource` boosts, plus this level's choices.
4. `bg3_test_run_level <class> <level>` runs every automated case; player cases: `bg3_test_stage`, cast, `bg3_test_verify`.
5. Save only after verify/cleanup. Staged spawns and test boosts must never end up in a save.

`bg3_test_script` writes the human reading script (`<mod>/docs/test-scripts/<Class>[_L<n>].md`).
`bg3_game_restart(deploy_layer)` kills the game, runs the layer's `deploy` command, relaunches through
Steam with `-continueGame` (the newest save) and waits until a character is loaded.

## Test builds and plans
A `[[build]]` is one test character: `class`, optional `subclass`, `levels = [from, to]`, optional `from`
(the build whose final save it starts from), `save_as`, `ability` (for ASIs), `feats = { "8" = "..." }`,
`spells = { "5" = ["Spell_Id"] }` (pins), `passives = { "19" = ["Boon"] }`. Cases are assigned to builds:
by `build`, else by `subclass`, else class-wide cases are spread across the builds covering their level, so
each spell is tested once across all runs. `bg3_test_plan(layer, build)` writes the per-level script: the
exact subclass, spells (every tested spell learned by its test level; the rest named as filler), feat and
other choices, then the tests that run there. `bg3_test_run_level(layer, level, build=...)` runs them.

Case kinds beyond spells: `console = "!mycommand X"` + `expect_log = "PASS marker"` (+ `fail_log`) runs a
mod's own test command; `retries = N` re-runs a failed case (save-based effects);
`status_applied_any = [...]` passes if any listed status lands.

`bg3_lint_progressions(layer)` checks progression data statically: invalid node UUIDs (the game drops the
node), selectors pointing at lists no layer defines, and stacked choices (several nodes for one
table+level that each grant selectors/feats: all load, so the player is asked twice).

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
target = "A"                 # "host", a spawn alias, or "ground" (cast at a point `distance` m ahead)
mode = "auto"                # default; or "player" / "script"
prep = "..."                 # optional: shown as "Before casting: ..."
grant_passive = "Feature"     # optional action instead of/before a spell: add a passive after the before-snapshot
repeat = 4                    # optional (auto/script): cast the spell N times, repeat_wait seconds apart (default 3)
instructions = "..."         # optional: extra step after the cast step
notes = "..."                # optional: shown at the end of the case
combat = true                # default: true when any spawn is hostile
initiative = "host_first"    # default in combat: temporary Initiative(50) boost on the host
refill = true                # default: host action resources (slots, action points...) restored to max and spell cooldowns cleared first
safety = true                # default: kill spawns + heal if a party member drops below safety_floor
safety_floor = 35            # percent HP
spawn = [{ as = "A", template = "wolf", faction = "hostile", hp = 40, distance = 8 }]
setup = [{ target = "host", hp = 2 }, { target = "host", status = "FRIGHTENED", turns = 10 },
         { target = "A", boost = "Resistance(Fire,Resistant)" }, { target = "host", max_hp = 20 },
         { target = "host", passive = "SomeFeature" },   # passive: added for the case, removed at cleanup
         { target = "A", dead = true }]                  # a corpse, e.g. for revive spells
expect = [
  { acted_first = true },                                   # first recorded turn was the host's
  { cast = true },                                          # the host cast `spell` (or cast = "OtherSpell")
  { target = "A", hp_change = [-15, -6], damage_type = "Force" },
  { target = "A", dead = true },
  { target = "host", hp = "full" },                         # or an exact number
  { target = "host", max_hp_change = 40 },
  { target = "host", status_applied = ["MAGE_ARMOR"] },     # event seen during the run
  { target = "host", status_removed = ["FRIGHTENED"] },     # present before, gone after
  { target = "host", status_present = ["X"], status_absent = ["Y"] },
  { resource = "SpellSlot", level = 1, change = -1 },       # host by default; level 0 for non-slot resources
  { roll = "ath", pass = [30, 30] },                        # passes out of a `rolls` batch (below)
]
rolls = [{ as = "ath", type = "SkillCheck", id = "Athletics", dc = 8, n = 30 }]  # real passive rolls by the host
                             # after setup (SavingThrow/SkillCheck/RawAbility); add a control batch (another
                             # ability/skill) to show a boost doesn't leak
```
Templates: `wolf`, `boar`, `bear`, `skeleton`, `zombie`, or any root-template GUID. Factions: `hostile`,
`friendly`, `neutral`, or a faction GUID. `bg3_test_list` validates every case against the index
(unknown spells, statuses, aliases, keys).

Scripted casts (`Osi.UseSpell`) skip the target's saving throw: the effect lands as if the save failed (seen
in game 2026-10-01: a wolf's Bash knocked the host prone 25/25 with no save in the combat log). Test save
modifiers with `rolls`, or with a real enemy turn, not with a scripted save spell.

## How it works
The harness (`bg3data/lua/harness.lua`) is written as a loose file under `<game>/Data/Public/BG3DataTest/`
and loaded through the SE console into the global `BG3T`. It re-installs itself after a Lua reset or a
version change. It tracks everything it creates (spawns, boosts tagged `BG3Test`, statuses) so cleanup is
complete, records Osiris events (StatusApplied/Removed, CastedSpell, UsingSpellOnTarget, AttackedBy,
Died, TurnStarted, CombatStarted/Ended, LeveledUp) for tracked characters, and runs the safety watch on
HitpointsChanged. Staging order matters: a hostile spawn starts combat and rolls initiative the moment it appears, so the
recorder, safety watch and Initiative boost are set first, creatures spawn neutral, get their setup, and
only then turn hostile. Verify also removes statuses the run left on the host. Listeners dispatch through
`BG3T.on`, so a newer harness replaces handlers without stacking listeners. Staging state is kept in the
cache (`test_state.json`), so it survives an MCP restart.

## Known limits
- The Nautiloid tutorial (`TUT_SUMMON_BLOCK`) blocks summons: test summon spells after the crash site.
- The engine can't require a spell's second target to be within a distance of the first. Check that by eye.
- `Ext.StaticData` reads occasionally come back empty for a tick; host snapshots retry.
