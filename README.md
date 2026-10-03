# bg3-data-mcp

An MCP server (and CLI) for **Baldur's Gate 3 modding**: layered game-data lookup, static checks that catch
what the engine silently ignores, in-game testing through Script Extender, and export to the Larian Toolkit
for mod.io publishing. Works natively on Windows or under WSL, finds the game and tools itself, and isn't
tied to any one mod.

- **Layered data:** the base game read straight from the installed paks (`Shared` → `Gustav` → `GustavX` →
  hotfix `Patch*.pak`, via LSLib's `Divine.exe`), so it always matches the current patch, plus your mod
  layers (unpacked folders or `.pak`s) in load order. Every resolved field says which layer set it.
  Indexed: stats, English localization, root templates, progressions, lists, class descriptions, level
  maps, action resources, feats, MultiEffectInfos and named `.lsfx` effects.
- **Static checks:** `bg3_lint_stats` (values, functions and fields no shipped content uses - the engine
  drops them silently - missing references, spells that can't resolve) and `bg3_lint_progressions`
  (invalid node UUIDs, dangling lists, stacked choices).
- **Running game (Script Extender):** Lua eval, console commands, hot-loading stats without a restart,
  ground-truth comparison of what the game actually loaded, and a test framework: level up by XP,
  check a level against its progression, and TOML test cases run as real encounters
  ([docs/TESTING.md](docs/TESTING.md)).
- **Deploy and Toolkit:** pack/deploy/enable any mod (`bg3_deploy`), restart the game into the newest
  save, and generate or diff the Toolkit's editor copy ([docs/TOOLKIT.md](docs/TOOLKIT.md)).

The SQLite index is cached per machine (see Configuration). A layer is rebuilt only when its sources'
timestamps or sizes change; rebuilds are atomic, so a failed rebuild keeps serving the previous index.

Not affiliated with Larian Studios. Requires a legal copy of the game; nothing from the game is
redistributed - data is read from your own install.

## Resolution rules (stats)
- The highest-ranked `new entry NAME` among the active layers wins.
- `using "NAME"` (the entry's own name) inherits the previous layer's definition of NAME.
- `using "OTHER"` inherits OTHER, resolved across all active layers.
- No `using`: the entry stands alone. A redefinition without `using` replaces the earlier one.
- Every resolved field records the layer and source that set it (e.g. `base/GustavDev`, `dnd55e`).

## Tools
| Tool | Purpose |
| --- | --- |
| `bg3_layers` | layers, load order, source timestamps, counts |
| `bg3_refresh(force)` | re-index changed layers (`force`: a layer name or `all`) |
| `bg3_add_mod_layer(name, path, position)` / `bg3_remove_mod_layer(name)` | manage mod layers |
| `bg3_get_entry(name, layers)` | resolved stats entry: `using` chain plus the source of each field |
| `bg3_diff(name, layer)` | what a layer changes about an entry compared with the layers below |
| `bg3_search(text, type, field, defined_in)` | find entries by name or field value; `defined_in="dnd55e"` = only what that layer defines |
| `bg3_references(token)` | everything mentioning a name or GUID (stats, templates, progressions, lists) |
| `bg3_loca(query)` | handle lookup or text search |
| `bg3_template(key)` | root template resolved through its ParentTemplateId chain |
| `bg3_progression(key, level)` | class/subclass nodes merged by node UUID |
| `bg3_spell_list(key)` | spell/passive list by UUID or name |
| `bg3_spell_visuals(name)` | a spell's effects, animations, sounds and icon |
| `bg3_similar_spells(damage_type, school, spell_type, level, keyword)` | spells like X, with visual kits to borrow |
| `bg3_effect(guid)` | a MultiEffectInfo (or effect resource): component VFX names, bones, duration, looping, `.lsfx`, and who uses it |
| `bg3_search_effects(text)` | find effects by name, every word in any order ("necrotic beam") -> GUIDs ready for `*Effect` fields |
| `bg3_game_restart(deploy_layer)` | kill the game, run the layer's `deploy` command, relaunch via Steam into the newest save, wait until loaded |
| `bg3_level_up(levels)` / `bg3_level_check` | grant exactly the XP for the next level; compare the host with its class/subclass progressions |
| `bg3_test_list` / `bg3_test_script` / `bg3_test_run` / `bg3_test_stage` / `bg3_test_verify` / `bg3_test_cleanup` | data-driven in-game test cases (TOML in the mod repo) run as real encounters - scripted, real-roll or AI-driven: see [docs/TESTING.md](docs/TESTING.md) |
| `bg3_test_draft(passives / key+level)` | draft TOML test cases from a feature's stats (targets, forced saves/hits, reactions, resource pools, non-overlapping controls), with confidence + TODOs |
| `bg3_test_spell_check(spell)` / `bg3_save_spells(ability)` | pre-checks before writing a test: can the AI cast it, is its save the spell's own roll (interrupt-visible) or a status/surface/passive save; AI-castable spells by save ability |
| `bg3_lint_stats(layer)` / `bg3_lint_progressions(layer)` | static checks before deploying: values the engine silently drops (vocabulary learned from the other layers), tooltip-only names used as functors, missing references, unknown resources; progression UUIDs, dangling lists, stacked choices |
| `bg3_toolkit_status` / `bg3_toolkit_export` / `bg3_toolkit_check` | Larian Toolkit (mod.io publishing): where the Toolkit expects the mod, generate its editor copy (.stats/.tbl) from the game-ready files, and diff the two copies - formats learned from vanilla + dnd55e editor data |
| `bg3_ingame_check(layer)` | every stats entry a layer defines vs what the running game loaded (invalid values the engine dropped) |

## Install
Works natively on **Windows** or under **WSL**; the game, Script Extender and LSLib are Windows programs either way.
Needs [uv](https://docs.astral.sh/uv/), Python 3.11+, and LSLib v1.20.4+
([releases](https://github.com/Norbyte/lslib/releases); Vortex's bundled `divine.exe` is too old for current LSF files).

**Windows (PowerShell):**
```powershell
git clone <this repo> C:\Mods\bg3-data-mcp
cd C:\Mods\bg3-data-mcp
copy layers.example.json layers.json        # then list your mod folders (see below)
uv run python tests\env_check.py            # what it found: game, Divine.exe, mod managers, Script Extender
uv run bg3-data refresh                     # first index build: a few minutes (extracts from the game paks)
claude mcp add bg3-data -- uv run --quiet --directory C:\Mods\bg3-data-mcp bg3-data-mcp
```

**WSL:** keep the environment on the Linux filesystem (fast), the project anywhere:
```bash
export UV_PROJECT_ENVIRONMENT=$HOME/.cache/bg3-data-mcp/venv
cp layers.example.json layers.json          # then list your mod folders
uv run python tests/env_check.py && uv run bg3-data refresh
claude mcp add bg3-data -e UV_PROJECT_ENVIRONMENT=$HOME/.cache/bg3-data-mcp/venv -- \
  uv run --quiet --directory /mnt/d/path/to/bg3-data-mcp bg3-data-mcp
```

### Configuration (`layers.json`)
Only your mod layers are required; everything machine-specific is **discovered** and can be overridden.
Paths may be written Windows-style (`D:\\Mods\\MyMod`) or WSL-style (`/mnt/d/Mods/MyMod`) - both work on both.
```json
{
  "mods": [
    {"name": "dnd55e", "path": "C:\\BG3Mods\\dnd55e"},
    {"name": "mymod", "path": "C:\\BG3Mods\\MyMod", "tests": "tests/bg3", "deploy": "optional custom command"}
  ],
  "base":  {"game_data": "E:\\Games\\Baldurs Gate 3\\Data"},
  "divine": "C:\\Tools\\LSLib\\Packed\\Tools\\Divine.exe",
  "game":  {"launcher": "auto", "profile": "Public", "larian_dir": "...", "steam_exe": "...", "game_exe": "..."}
}
```
| Setting | Discovered from (when omitted) |
| --- | --- |
| `base.game_data` | Steam (registry + every library in `libraryfolders.vdf` + the app manifest), GOG (registry), common folders |
| `divine` | `BG3_DIVINE` env, `PATH`, `*\\LSLib\\Packed\\Tools\\Divine.exe` under your user folder / C: / D:, Vortex's copy (last) |
| `game.larian_dir` | `%LOCALAPPDATA%\\Larian Studios\\Baldur's Gate 3` (Mods, PlayerProfiles, Script Extender Logs) |
| `game.launcher` | `steam` (`steam.exe -applaunch 1086940`) for Steam installs, else `direct` (`bin\\bg3_dx11.exe`); GOG uses direct |
| `game.profile` | `Public` (the profile whose `modsettings.lsx` and saves are used) |
| mod `deploy` | none: `bg3_deploy` packs `Mods/<folder>` + `Public/<folder>` with Divine, deploys and enables the mod |
| cache | `%LOCALAPPDATA%\\bg3-data-mcp\\cache` (Windows), `~/.cache/bg3-data-mcp/cache` (WSL); `BG3_DATA_CACHE` overrides |

### Mod managers
`bg3_environment` reports what manages the game's Mods folder. **Vortex** (detected from its deployment
manifest) and **BG3 Mod Manager** (detected while running) rewrite `modsettings.lsx` when they deploy or
export a load order, which disables mods they don't manage: re-run `bg3_deploy` afterwards (it re-enables the
mod after its dependencies), or add your dev mod to the manager. The **in-game mod manager** lists local paks
under Installed and needs nothing extra.

## Script Extender bridge (running game)
These tools talk to the live game through the SE console, using a persistent injector process
(`bg3data/ps/se_inject_server.ps1`, AttachConsole + WriteConsoleInput, so no window focus is needed; ~0.34 s per
call, with the one-shot `se_inject.ps1` as fallback). Output is read back from the current
run's `Extender Runtime` log. Log file names are UTC; the bridge compares real modification times with
the game's start time.

| Tool | Purpose |
| --- | --- |
| `bg3_se_status` | game running? PID and start time, this run's log, latest game state |
| `bg3_se_eval(code, context)` | run Lua (server/client); returns the return value as JSON plus printed lines. **Runs in your live game.** |
| `bg3_se_command(line)` | one console line (e.g. `!apofeature X`), plus the log lines it produced |
| `bg3_se_log(filter, lines)` | tail this run's log |
| `bg3_se_live_entry(name, layers)` | ground truth: the entry as the game loaded it vs the index, field by field |

### Hot loading (no restart)
| Tool | Purpose |
| --- | --- |
| `bg3_se_hot_load(layer, files, loca)` | mirror a mod layer's stats `.txt` as loose files under `<game>/Data/Public/BG3DataHot_<layer>/`, `Ext.Stats.LoadStatsFile(…, true)` each, `Ext.Stats.Sync` every entry, and push its English loca (`Ext.Loca.UpdateTranslatedString`) |
| `bg3_se_hot_clean` | delete the loose `BG3DataHot_*` folders from the game install |
| `bg3_se_reset_lua` | console `reset`: reload SE Lua for mods loaded at startup |

SE only reads safe **relative** paths through the game's file system (absolute paths are rejected by
`IsSafeRelativePath`), which is why layers are mirrored as loose files. Entries created after startup must
be synced; the tool does that. Hot loading covers **stats, loca and Lua**. Progressions, lists and templates
load at startup and need a restart with a pak. Stats files with `//` comments load fine (the lines are skipped).
Measured 2026-09-30: all 661 Apotheosis entries in 3.0 s and 755 loca strings in 2.6 s. The edit → hot-load
one file → re-test loop takes about 3 s. Test-only: don't save while relying on hot-loaded entries.

Each eval is wrapped in unique BEGIN/END markers and `pcall`, so Lua errors come back as errors rather
than timeouts. SE replaces `load()` (its second argument must be an environment table). Console access is
serialised. Functor fields (`SpellSuccess`...) are exposed by SE as parsed userdata and can't be compared
as text. SE reports `ComboCategory` as empty.

## Hardening
- One SQLite connection shared by MCP worker threads, serialised with a lock (WAL mode).
- Every tool catches errors and returns a readable message; free-text inputs are capped at 500 characters
  (SQLite's LIKE limit), `limit` is clamped to 1-500, and output is capped at 24,000 characters.
- `%` and `_` in user text match literally (LIKE ESCAPE).
- Resolved entries are cached per layer stack; the cache is cleared on any rebuild.
- Base extraction goes to a temp directory and is swapped in, so a failed extraction keeps the old cache.
- The first call after a game patch rebuilds `base` (about 80 s). Run `uv run bg3-data refresh` after
  patches to do it ahead of time.

## Tests
- `uv run python tests/stress_test.py` drives the real server over stdio with the official MCP client:
  protocol, correct answers, bad input, concurrency (400 calls, 32 at a time), refresh under load,
  `.pak` layer add and remove, and a full resolution sweep. It writes `tests/STRESS_REPORT.md`.
- `uv run python tests/ingame_check.py [--layers dnd55e] [--sample 400]` compares resolved entries with the
  running game through SE. The deployed paks must match the layers. It writes `tests/INGAME_REPORT.md`.
  The first run (2026-09-30) compared 400 entries (200 mod-overridden) and 7,844 fields: 18 mismatches
  (0.23%). 16 were `ComboCategory` (SE doesn't expose it) and 2 were one quirk (`MULTIATTACKDEFENSE`: the
  game didn't inherit DisplayName through dnd55e's `using`). A targeted test of the 72 cases where
  "override re-parents via `using`" and "`using` ignored on override" predict different values sided with
  the resolver's model 78:1.

## Known limits
- `SpellAnimation` GUIDs are animation slot keys mapped per race and body across about 2,500 content banks,
  so they aren't resolved to names yet. Copy them between spells as-is.
- About 0.7% of MultiEffectInfo components point at effect resources outside the indexed banks.
