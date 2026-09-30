# bg3-data-mcp

Layered Baldur's Gate 3 game-data lookup, as an MCP server (and a CLI).

- **Base game layer (always on):** read straight from the installed game's paks: `Shared` → `Gustav`
  → `GustavX` → hotfix paks (`Patch*.pak`, in number order), using LSLib's `Divine.exe` with filtered
  extraction. It always reflects the current patch, unlike a one-time unpack that goes stale. The
  Honour Mode and Photo Mode modules are excluded by default.
- **Mod layers (additive, in load order):** unpacked mod folders (`Public/` + `Mods/`) or `.pak` files.
  Configured in `layers.json`, or added and removed at runtime.
- **Per-query layer choice:** omit `layers` for everything, or pass mod names to stack on base, e.g.
  `["dnd55e"]` gives vanilla + dnd55e.

Indexed: stats (`Stats/Generated/Data/*.txt`), English localization, root templates, progressions,
lists (spell/passive/skill/ability/equipment), **MultiEffectInfos** (what stats `*Effect` fields point to)
and **effect resources** (named `.lsfx` VFX from `Content/Assets/Effects` banks). The SQLite index lives in
`~/.cache/bg3-data-mcp/cache` (override with `BG3_DATA_CACHE`; keep it on the Linux filesystem). A layer
is rebuilt only when its sources' timestamps or sizes change, and the server re-checks at most once a
minute. Rebuilds are atomic: a failed rebuild keeps serving the previous index and `bg3_layers` reports it.

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

## Setup
```bash
# the environment lives on the Linux filesystem (fast); the project stays on /mnt/d
export UV_PROJECT_ENVIRONMENT=$HOME/.cache/bg3-data-mcp/venv
uv run bg3-data refresh            # first build: extracts from game paks (a few minutes)
uv run bg3-data entry Target_Heal --layers dnd55e
claude mcp add bg3-data -e UV_PROJECT_ENVIRONMENT=$HOME/.cache/bg3-data-mcp/venv -- \
  uv run --quiet --directory /mnt/d/BG3Modding/Mod_Projects/bg3-data-mcp bg3-data-mcp
```
`layers.json` holds the `Divine.exe` path (LSLib v1.20.4+; Vortex's bundled copy is too old for LSF v7)
and the game `Data` path.

## Script Extender bridge (running game)
These tools talk to the live game through the SE console, using `References/Dev/dnd55e-tools/se_inject.ps1`
(AttachConsole + WriteConsoleInput, so no window focus is needed). Output is read back from the current
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
