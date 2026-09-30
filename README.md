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

Indexed: stats (`Stats/Generated/Data/*.txt`), English localization, root templates, progressions and
lists (spell/passive/skill/ability/equipment). The SQLite index lives in `cache/`. A layer is rebuilt
only when its sources' timestamps or sizes change, and the server re-checks at most once a minute.

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
| `bg3_search(text, type, field)` | find entries by name or field value |
| `bg3_references(token)` | everything mentioning a name or GUID (stats, templates, progressions, lists) |
| `bg3_loca(query)` | handle lookup or text search |
| `bg3_template(key)` | root template resolved through its ParentTemplateId chain |
| `bg3_progression(key, level)` | class/subclass nodes merged by node UUID |
| `bg3_spell_list(key)` | spell/passive list by UUID or name |
| `bg3_spell_visuals(name)` | a spell's effects, animations, sounds and icon |
| `bg3_similar_spells(damage_type, school, spell_type, level, keyword)` | spells like X, with visual kits to borrow |

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

## Planned (phase 2)
Resolve effect GUIDs (stat `*Effect` fields → MultiEffectInfos → `.lsfx` effect names in `Effects.pak`),
so VFX can be searched by what they look like.
