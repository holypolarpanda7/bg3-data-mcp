# Larian Toolkit export (mod.io publishing)

mod.io publishing goes through the Larian Toolkit, and the Toolkit edits its **own copy** of a mod's data under
`Data/Editor/Mods/<mod>/`: `.stats` files for stats (spells, statuses, passives, interrupts, characters,
objects, weapons, armor) and `.tbl` tables for lists, progressions, action resources, class descriptions,
level maps and feats. The game loads the other copy (`Public/<mod>/Stats/Generated/Data/*.txt`, `*.lsx`).
A mod written as game-ready files needs its editor copy generated, and the two must stay in step.

| Tool | Does |
| --- | --- |
| `bg3_toolkit_status(layer)` | Where the Toolkit expects the mod (Data/Projects, Mods, Public, Editor/Mods), whether each is a junction to the repo or a separate copy, file counts, meta.lsx version |
| `bg3_toolkit_export(layer, dest, write)` | Generate the editor copy from the game-ready files. `write=False` (default) is a dry run with counts and warnings. `dest`: `toolkit` (game install) or `repo` (`<mod>/Editor/Mods/<mod>`). Close the Toolkit first |
| `bg3_toolkit_check(layer, dest)` | Diff the editor copy against an export of the game-ready files: missing / extra objects, differing fields. 0 problems = in sync |

## How it knows the formats
Nothing beyond "which file holds which data kind" is hard-coded. `learn()` (cached in the bg3-data cache):
- **field types** (`StringTableFieldDefinition`, enum names and versions, ...) from the vanilla editor files in
  the game install (`Data/Editor/Mods/{Shared,SharedDev,Gustav,GustavDev,GustavX}`) and any mod layer with an
  `Editor/` folder;
- **mappings** by joining mods that ship both copies (stats by name, tables by UUID): spells drop their
  `<SpellType>_` prefix, `MemoryCost` -> `SpellPrepareCost`, `WeaponTypes` -> `WeaponType`, progression
  `Name` -> `FSName` (the editor `Name` is a generated `New_Stat_N`), `PrimaryAbility 4` -> `Intelligence`,
  `uuid(AnimationName)` -> `uuid`, child lists (`SubClasses`, `Tags`) -> `;`-joined GUID lists;
- **`Using`** resolves to the parent's editor UUID: this mod, then its dependencies, then vanilla. A spell's
  parent is looked up in the file of its own SpellType; a status may inherit from another status type.
- **UUIDs** of existing editor objects are kept (re-exports produce small Toolkit diffs); new ones are uuid5.

Validation (2026-09-30): exporting dnd55e's game-ready files reproduces its hand-maintained editor copy for
8107 objects with 19 differences, which are drift between dnd55e's own two copies (a `Using` that points at
a different parent, a TargetCeiling).

Not yet: round-trip into the Toolkit itself (open, save, package) - planned with a small test mod.
