# bg3-data-mcp stress report

Run 2026-09-30 14:39 CDT. 64/64 checks passed.


## protocol
- PASS 24 tools: bg3_add_mod_layer, bg3_diff, bg3_effect, bg3_get_entry, bg3_layers, bg3_loca, bg3_progression, bg3_references, bg3_refresh, bg3_remove_mod_layer, bg3_se_command, bg3_se_eval, bg3_se_hot_clean, bg3_se_hot_load, bg3_se_live_entry, bg3_se_log, bg3_se_reset_lua, bg3_se_status, bg3_search, bg3_search_effects, bg3_similar_spells, bg3_spell_list, bg3_spell_visuals, bg3_template
- PASS first call (cold start incl. index check) 2.1s; startup 2.8s

## golden
- PASS Apotheosis upcast chains via dnd55e to base (bg3_get_entry, 4 ms)
- PASS dnd55e gap: vanilla 6th tier still 4d4 (bg3_get_entry, 3 ms)
- PASS base only: vanilla Chill Touch is ranged (bg3_get_entry, 2 ms)
- PASS +dnd55e: melee (2024) (bg3_get_entry, 1 ms)
- PASS diff shows dnd55e change (bg3_diff, 2 ms)
- PASS Psychic Veil moved 9->13 (bg3_progression, 2 ms)
- PASS without apotheosis: no removal (bg3_progression, 1 ms)
- PASS template chain (bg3_template, 2 ms)
- PASS MEI resolve + users (bg3_effect, 15 ms)
- PASS effect search, words any order (bg3_search_effects, 39 ms)
- PASS visual kit names effects (bg3_spell_visuals, 3 ms)
- PASS loca handle (bg3_loca, 2 ms)
- PASS search runs (may be empty upstream) (bg3_search, 18 ms)
- PASS references include progression (bg3_references, 25 ms)
- PASS spell list by UUID (bg3_spell_list, 3 ms)
- PASS similar spells (bg3_similar_spells, 186 ms)
- PASS layers + timestamps (bg3_layers, 2 ms)

## fuzz
- PASS unknown layer: "error: unknown layer(s) ['nope']; known: ['base', 'dnd55e', 'apotheosis']"
- PASS empty name: "'' not found in layers ['base', 'dnd55e', 'apotheosis']"
- PASS SQL injection name: "''; DROP TABLE stats; --' not found in layers ['base', 'dnd55e', 'apotheosis']"
- PASS empty search: 'error: `text` must not be empty'
- PASS literal percent: 'ALCH_POTION_REST_SLEEP_GREATER_RESTORATION (StatusData) [base]\nALCH_POTION_REST_SLEEP_LESS'
- PASS literal underscore: 'ABERRANT_SHAPE (StatusData) [base]\nABERRANT_SHAPE_REMOVE_VFX (StatusData) [base]\nABILITYSC'
- PASS unicode: 'no matches'
- PASS 100k-char input rejected cleanly: 'error: `text` is 100000 characters; the maximum is 500'
- PASS negative limit clamps: 'ABERRANT_SHAPE (StatusData) [base]'
- PASS huge limit is clamped to 500 results: 'ABERRANT_SHAPE (StatusData) [base]\nABERRANT_SHAPE_REMOVE_VFX (StatusData) [base]\nABILITYSC'
- PASS too-short token: 'error: `token` needs at least 3 characters'
- PASS bad GUID: 'error: expected a GUID like 4cab2089-4c14-44f6-9fe0-5421ec911552'
- PASS unknown GUID: 'no MultiEffectInfo or effect resource with GUID 00000000-0000-0000-0000-000000000000'
- PASS too-short effect search: 'error: `text` needs at least 3 characters'
- PASS impossible level: "no progression nodes for 'Soulknife'"
- PASS diff unknown layer: "layer 'nope' is not in the active stack ['base', 'dnd55e', 'apotheosis']"
- PASS bad handle: 'handle not found'
- PASS path-like key: "template '../../etc/passwd' not found"
- PASS bad layer path: "error: '/definitely/not/here' is neither a mod folder nor a .pak file"
- PASS remove unknown: "no mod layer 'nope'"
- PASS index intact after fuzzing (row counts {'stats': 22653, 'loca': 239109, 'templates': 26017, 'prog': 2468, 'lists': 928, 'mei': 3863, 'fx': 11314})

## concurrency
- PASS 400 calls x32 in 3.1s (130/s); p50 240 ms, p95 284 ms, max 473 ms

## memory
- PASS server RSS 105 MB

## refresh-under-load
- PASS forced apotheosis rebuild alongside 80 queries; failures []

## se
- PASS eval round-trip 2.6s: 'OK: 42'
- PASS Lua errors come back as errors, not timeouts
- PASS printed output captured
- PASS live entry vs index (2.6s):   29/29 fields match
- PASS log tail with filter
- PASS 6 concurrent evals serialised (53.7s total)
- PASS hot-load one file (2.6s): hot-loaded apotheosis: 1 entries synced (0 newly created in this session)
- PASS hot-load refuses the base layer
- PASS hot_clean removes loose files: removed: BG3DataHot_apotheosis, BG3DataHotProbe

## layers
- PASS added .pak layer in 1.9s: stats 22, loca 20, templates 0, prog 1, lists 0, mei 0, fx 0
- PASS duplicate layer rejected
- PASS defined_in scopes to the .pak layer: ['BLADESONG (StatusData) [stress_bladesinger]', 'BLADESONG_ARMOR (StatusData) [stress_bladesinger]']
- PASS diff on .pak entry BLADESONG: stress_bladesinger changes to BLADESONG:
- PASS removed .pak layer
- PASS layers.json content restored

## sweep
- PASS base: resolved 15754 entries in 0.7s, exceptions 0, unresolved `using` 0
- PASS base+dnd55e: resolved 19696 entries in 1.1s, exceptions 0, unresolved `using` 0
- PASS all: resolved 20346 entries in 1.2s, exceptions 0, unresolved `using` 1; e.g. ['TRUE_POLYMORPH @ apotheosis (Status_BOOST.txt) using YOURFALLENCOMRADE [UNRESOLVED]']
- PASS templates: 25819 chains walked in 1.5s; 0 reference a missing parent (vanilla has some)
- PASS effects: 3862 MultiEffectInfos, 12299 components, 80 with no indexed resource (0.7%) in 0.2s
- PASS progressions: 230 tables merged in 0.0s
