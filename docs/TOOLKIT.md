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

## Driving the Toolkit: packaging and mod.io publishing (2026-10-07)

`bg3_toolkit_ui(action, layer, apply)` / `python -m bg3data.toolkit_ui` drives `Glasses.exe` (Toolkit 4.1.1.6931813):

| action | does |
| --- | --- |
| `state` | Toolkit running, the open project (window title), the Project Settings window rectangle |
| `projects` | the project picker's list (names = each mod's meta.lsx Name) |
| `open` | launch through Steam (app 2934770) if needed and open the layer's project; waits for the title to show it |
| `publish_local` | Project Settings -> **Publish Local**: the Toolkit packs `<Mods>/<Folder>.pak` (checked by its mtime) and opens Explorer there (closed again) |
| `publish` | Project Settings -> **Publish** (mod.io). Dry run with a screenshot unless `apply=True`; apply needs a GREEN gate on HEAD and waits for the browser to open on the mod's mod.io File Manager page |

How: the picker (RadioButton rows, `m_OpenButton`) and menus (Project > Project Settings...) are WPF and driven through UI
Automation. The Project Settings window and the Message Log draw their own controls (no UIA children), so their buttons are
clicked at offsets from the window's bottom edge and the result is checked outside the Toolkit (files, browser windows).
Screenshots of every step land in `<cache>/toolkit/`.

Packing **rewrites meta.lsx**: dependency MD5s / versions / names are refreshed, and with Auto-increment ticked the mod's
own build number goes +1 per Publish Local / Publish. Both actions report the diff (`meta_changed`): commit it after a real
publish, revert it after a test run. Set the version with `bg3data.release prepare` before publishing.

After a publish (mod.io's side, not automated): wait for the scan, fill in the profile and media for a new mod, set
dependencies, then **Go live**.
