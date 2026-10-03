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
- `mode = "ai"`: a spawn (`caster`) casts through its own AI on its own turns while the host ends turns under
  Sanctuary. Real rolls AND interrupts/reactions: the only way to exercise what an enemy's spell does to the
  party. Every other spell of the caster is put on cooldown each of its turns, so it can only use the case's
  spell (`cast_only` proves it).
The spell must be learned through the class (SpellBook source is not `Osiris`) for auto and player cases.
Every verdict states which kind of run it was.

## Which method
| What you're testing | Method |
| --- | --- |
| A spell's effects: damage, statuses, healing, summons, costs (from data) | `auto` / `script`: a scripted cast. Deterministic and fastest. |
| Something that depends on the target's saving throw (save bonuses, advantage, floors) | `script` + `real_rolls = true` (the save is rolled), or `ai` |
| Anything that reacts to a roll or a cast: interrupts (Legendary Resistance-style), reactions | `ai` only - Osiris casts never raise OnPostRoll interrupts |
| Turn order, enemy turns | `end_turns` (any mode) or `ai` |
| A spell's real slot/action cost | `player` (hotbar spot check) |

Before writing an `ai` case, run `bg3_test_spell_check(spell)`: it says whether the AI can cast the spell at all
(`AIFlags CanNotUse`), whether its save is the spell's own roll or comes from a status/surface/passive, and what
else will trip the run. `bg3_save_spells(ability)` lists AI-castable spells whose own roll is a save of that
ability. Validation rejects `ai` cases that can't work (CanNotUse, RequirementConditions, a `spell_only` save the
spell doesn't roll).

## Speed
- The SE bridge keeps one PowerShell injector running (`ps/se_inject_server.ps1`) instead of starting
  `powershell.exe` and compiling the C# helper per call: ~0.34 s per `bg3_se_eval` instead of ~2.3 s.
- Waits are event-driven: casts are awaited by their CastedSpell event and effects by a 0.8 s quiet period
  (`wait`, `repeat_wait`, `casts.wait` are now caps, not sleeps); End Turn waits for the turn to pass instead of
  fixed sleeps.
- `ai_samples = N` stops an `ai` run once N matching saves are recorded instead of playing every `ai_rounds`.
- Prefer exact per-roll checks (`saves.min_total`) over statistics: one low natural roll is decisive.
- Measured 2026-10-02: 12 deterministic cases 380 s -> 133 s.

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
casts = [{ spell = "Shout_X" }, { spell = "Shout_Y", target = "A", wait = 2 }]  # optional: scripted casts in order
                             # target = "ground", distance = 0: at the host's spot (e.g. a Darkness cloud)
                             # after setup, before `spell` (a feature's setup steps, e.g. pick then use)
caster = "A"                 # optional: a spawn casts the case's `spell` (casts entries take `by = "A"`)
real_rolls = true            # optional: the cast rolls saves/attacks for real (strips Osiris' IgnoreSpellRolls)
mode = "ai"                  # with caster = spawn: its AI casts `spell` on its turns (see Which method)
ai_rounds = 12               # ai: max host turns to end; ai_samples = 6 stops early once 6 matching saves exist
ai_keep = ["..."]            # ai: extra spells the caster may use; sanctuary = false lets the AI target the host
reactions = ["Fate"]         # your interrupts left ON (name substrings, "*" = all). Default: all OFF in auto/script
                             # mode (Shield, Arcane/Projected Ward, opportunity attacks change what a case measures),
                             # all on in ai mode. Restored at cleanup.
clear_between = ["PRONE"]    # optional, with repeat: statuses removed from the target before each cast
end_turns = 7                # optional (combat): after the cast, end the host's turn N times via the HUD's
                             # End Turn (Osi.EndTurn doesn't end it), waiting for the host's turn each time
instructions = "..."         # optional: extra step after the cast step
notes = "..."                # optional: shown at the end of the case
combat = true                # default: true when any spawn is hostile; "after_setup" starts the fight
                             # after the before-snapshot (test "when you roll Initiative" features)
initiative = "host_first"    # default in combat: temporary Initiative(50) boost on the host
refill = true                # default: host action resources (slots, action points...) restored to max and spell cooldowns cleared first
safety = true                # default: kill spawns + heal if a party member drops below safety_floor
safety_floor = 35            # percent HP (safety = false for cases that need the host near 0 HP, e.g. Last Stand:
                             # the watch heals a party member below the floor and would undo `hp = 1`)
spawn = [{ as = "A", template = "wolf", faction = "hostile", hp = 40, distance = 8 }]
setup = [{ target = "host", hp = 2 }, { target = "host", status = "FRIGHTENED", turns = 10 },
         { target = "A", boost = "Resistance(Fire,Resistant)" }, { target = "host", max_hp = 20 },
         { target = "host", passive = "SomeFeature" },   # passive: added for the case, removed at cleanup
         { target = "A", dead = true },                  # a corpse, e.g. for revive spells
         { target = "host", resource = "SpellSlot", level = 1, amount = 0 },  # set a pool's current amount
                                                         # (a tick after the rest: boost-granted pools exist then)
         { target = "A", status = "INTERDICTED", turns = -1, by = "host" }]  # by: the status's cause
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
  { consecutive_turns = "host", count = [2, 5] },          # turns in a row right after the cast (with end_turns)
  { took_turn = "A" },
  { target = "host", status_applied_count = { status = "PRONE", count = [0, 0] } },  # times it landed
  { cast_only = "A" },                                      # the spawn cast nothing but the case's spell (ai)
  { resource = "EpicBoonFate", level = 0, amount_change = -1 },  # the character's pool before/after (not a cost)
  { resource = "Movement", level = 0, max_change = 9 },     # its maximum
  { target = "host", skill = "Athletics", change = 2 },     # exact skill bonus change; also ability = "Strength"
  { cast_count = { by = "A", count = [1, 99] } },           # casts of the case's spell by a spawn
  { interrupt_used = { name = "Interrupt_X", count = [1, 9] } },  # used N times (and how often considered)
  { target = "B", saves = { ability = "Strength", spell_only = true, n = [1, 99], failed = [0, 0], min_total = 16 } },
                             # recorded saving throws: spell_only = the spell's own roll (not a status/surface
                             # tick); min_total = every total at least this (exact floor check);
                             # effect_status = "X" counts X landing per failed roll, rescued = true: never                                      # A took a turn after the cast
  { roll = "ath", pass = [30, 30] },                        # passes out of a `rolls` batch (below)
]
rolls = [{ as = "ath", type = "SkillCheck", id = "Athletics", dc = 8, n = 30 }]  # real passive rolls by the host
                             # after setup (SavingThrow/SkillCheck/RawAbility); add a control batch (another
                             # ability/skill) to show a boost doesn't leak
```
Templates: `wolf`, `boar`, `bear`, `skeleton`, `zombie`, or any root-template GUID. Factions: `hostile`,
`friendly`, `neutral`, or a faction GUID. `bg3_test_list` validates every case against the index
(unknown spells, statuses, aliases, keys).

Scripted casts (`Osi.UseSpell`) are queued with the `IgnoreSpellRolls` cast option, so by default the target's
saving throw is never rolled and the effect lands as if it failed. `real_rolls = true` strips that option from the
queued request (Ext.System.ServerCastRequest.OsirisCastRequests), so the save is rolled for real (verified
2026-10-01: wolf Bash vs the host, prone 5/8 with real Strength saves). Passive rolls (`rolls`) and status-tick
saves never go through OnPostRoll interrupts.

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

## Verified engine facts (2026-10-01/02)
- `Osi.UseSpell` queues casts with IgnoreSpellRolls (+IgnoreHasSpell/CastChecks/TargetChecks): no save, no attack
  roll, and no OnPostRoll interrupts. `real_rolls` strips IgnoreSpellRolls; interrupts still never fire for it.
- OnPostRoll interrupts fire only for a spell's own rolls - not passive rolls (`RequestPassiveRoll`), status
  OnApplyRoll/OnTickRoll saves, surface saves, or saves from passives (e.g. trip-on-hit).
- `MinimumRollResult(SavingThrow, N)` floors only some real spell saves, even unconditionally.
- `SavingThrowRolledEvent` fires twice per roll with opposite Success; for a spell's own roll the event's Target is
  the caster and Source the saver. The harness dedupes per roll and decides success as total >= DC.
- `Osi.RemoveSpell` doesn't remove a template's innate attacks: the harness locks them with cooldowns.
- Writing a cast's pre-rolled saves (`SpellCastRolls`) back from Lua froze the server thread: don't.
- The first launch after quitting a loaded game is always in no-mods safe mode; restart handles it.
- Save events show the dice BEFORE interrupts. Judge an interrupt by outcome (`saves.effect_status`: did the
  effect land for each failed roll; `rescued = true`: never) and by `interrupt_used` (the harness records
  InterruptConsidered / InterruptUsed by name).
- Legendary Resistance needs its status (`LEGENDARY_RESISTANCE_<ABILITY>`, which sets 3 charges);
  `ActionResource(LegendaryResistanceCharge, ...)` only raises the maximum, leaving 0 charges.
- **Reactions and interrupts never fire on rolls of a spell the caster got through `Osi.AddSpell`** (nor on
  Osiris casts). Use a creature whose template knows the spell natively (`bg3_save_spells` lists them; staging
  warns when it had to add the spell). Found 2026-10-02: Larian's own Fighter Indomitable and Bardic Inspiration
  never prompted vs script-added spells; Indomitable Might rescued 2/2 vs a real Shadow's native Strength Drain.
- Player reactions do fire in spawned fights (Arcane Ward, Shield) - they show up as CastedSpell of the reaction's
  spell. Roll-adjusting interrupts (SetRoll/AdjustRoll) cast nothing: judge them by outcome (`saves.effect_status`).
- `Ext.Events.ExecuteFunctor` is NOT raised for interrupt Properties - a Lua hook can't adjust them.
- `IsSetInterruptInteresting(n)` didn't fire for SetRoll(n) with n below 20 (it seems to read n as the total);
  `IsFlatValueInterruptInteresting(30)` = "this roll failed" works.
- An `InterruptDecision` component appears when an interrupt is UNLOCKED, not when it's checked on a roll, and
  `ServerInterruptUsed` (one-frame, deferred) is unreliable - the harness's InterruptConsidered/Used events are
  hints only.
- Roll interrupts on ATTACKS must say whose roll: `IsFlatValueInterruptInteresting(8, context.Source)` (as base Cutting
  Words). Without the second argument a reaction to an enemy's attack never fires (Boon of Fate, 2026-10-02).
- A scripted melee attack from range makes the host walk in; the target's opportunity attack then cancels the
  attack (no damage). Give the target `ActionResourceBlock(ReactionActionPoint)` in setup for melee cases.
- The host's own features interfere: an Abjuration wizard's Arcane Ward absorbs the damage a Last Stand-style case
  needs (`remove_status = "ARCANE_WARD*"`), and the harness safety watch heals below 35% (`safety = false`).
- An NPC's AI decides its own interrupts and may decline (Legendary Resistance vs Strength Drain was declined).
- `InterruptPreferences.Preferences`: set keys one by one - assigning the whole map back wipes it.

Stats facts found while testing (2026-10-02):
- `IF()` boosts on a passive are re-evaluated only on its `BoostContext` events. Without one, a condition stays as it
  was when the passive was added. Status-conditioned resistances: `BoostContext OnStatusApplied;OnStatusRemoved` +
  `HasStatus('X', context.Source)` (dnd55e Fractured_6_BrainsAndBrawn).
- Caster-side Disadvantage on a spell's saves = `UnlockSpellVariant(<spell conditions>, ModifySavingThrowDisadvantage())`
  on the caster (Heightened Spell's mechanism). A target-side status/aura boost did nothing for spell saves.
- `GainTemporaryHitPoints(10)` in the OnApplyFunctors of a duration-0 status granted nothing (Fortifying Light,
  2026-10-02; it is a real functor); a status with `Boosts TemporaryHP(N)` works.
- `Kill()` works in a spell's properties but did nothing in an interrupt's Properties; apply a status whose
  `OnApplyFunctors` is `Kill()` instead (Headshot, 2026-10-02).
- In SE Lua, `stat.SpellProperties`/`SpellSuccess` are parsed functor arrays, not text: read `TooltipDamageList`/`DamageType`.
- A container spell with 44 children (`ContainerSpells` ~2080 chars) hangs the game at LoadModule; 43 / 2030 loaded.
  `bg3_lint_stats` reports SIZE issues; `bg3_game_restart` reports a hung load after ~60 s Not Responding.

## Known limits
- The Nautiloid tutorial (`TUT_SUMMON_BLOCK`) blocks summons: test summon spells after the crash site.
- The engine can't require a spell's second target to be within a distance of the first. Check that by eye.
- `Ext.StaticData` reads occasionally come back empty for a tick; host snapshots retry.

Turn order counts creatures' turns only. The engine still fires TurnStarted for a creature that can't act
(an INCAPACITATED status: sleeping, frozen) and for scenery items with an environment turn (the beach's
clamshells), so those are recorded with `incap` / `item` and left out (seen in game 2026-10-01).
