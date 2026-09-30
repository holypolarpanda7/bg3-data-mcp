# bg3-data-mcp stress report

Run 2026-09-30 14:09 CDT. 55/55 checks passed.


## protocol
- PASS 16 tools: bg3_add_mod_layer, bg3_diff, bg3_effect, bg3_get_entry, bg3_layers, bg3_loca, bg3_progression, bg3_references, bg3_refresh, bg3_remove_mod_layer, bg3_search, bg3_search_effects, bg3_similar_spells, bg3_spell_list, bg3_spell_visuals, bg3_template
- PASS first call (cold start incl. index check) 1.8s; startup 2.4s

## golden
- PASS Apotheosis upcast chains via dnd55e to base (bg3_get_entry, 2 ms)
- PASS dnd55e gap: vanilla 6th tier still 4d4 (bg3_get_entry, 2 ms)
- PASS base only: vanilla Chill Touch is ranged (bg3_get_entry, 1 ms)
- PASS +dnd55e: melee (2024) (bg3_get_entry, 2 ms)
- PASS diff shows dnd55e change (bg3_diff, 2 ms)
- PASS Psychic Veil moved 9->13 (bg3_progression, 2 ms)
- PASS without apotheosis: no removal (bg3_progression, 1 ms)
- PASS template chain (bg3_template, 2 ms)
- PASS MEI resolve + users (bg3_effect, 16 ms)
- PASS effect search, words any order (bg3_search_effects, 36 ms)
- PASS visual kit names effects (bg3_spell_visuals, 3 ms)
- PASS loca handle (bg3_loca, 2 ms)
- PASS search runs (may be empty upstream) (bg3_search, 18 ms)
- PASS references include progression (bg3_references, 24 ms)
- PASS spell list by UUID (bg3_spell_list, 2 ms)
- PASS similar spells (bg3_similar_spells, 190 ms)
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
- PASS 400 calls x32 in 2.7s (148/s); p50 209 ms, p95 246 ms, max 264 ms

## memory
- PASS server RSS 104 MB

## refresh-under-load
- PASS forced apotheosis rebuild alongside 80 queries; failures []

## layers
- PASS added .pak layer in 1.7s: stats 22, loca 20, templates 0, prog 1, lists 0, mei 0, fx 0
- PASS duplicate layer rejected
- PASS defined_in scopes to the .pak layer: ['BLADESONG (StatusData) [stress_bladesinger]', 'BLADESONG_ARMOR (StatusData) [stress_bladesinger]']
- PASS diff on .pak entry BLADESONG: stress_bladesinger changes to BLADESONG:
- PASS removed .pak layer
- PASS layers.json content restored

## sweep
- PASS base: resolved 15754 entries in 0.6s, exceptions 0, unresolved `using` 0
- PASS base+dnd55e: resolved 19696 entries in 0.9s, exceptions 0, unresolved `using` 0
- PASS all: resolved 20346 entries in 1.0s, exceptions 0, unresolved `using` 1; e.g. ['TRUE_POLYMORPH @ apotheosis (Status_BOOST.txt) using YOURFALLENCOMRADE [UNRESOLVED]']
- PASS templates: 25819 chains walked in 1.3s; 0 reference a missing parent (vanilla has some)
- PASS effects: 3862 MultiEffectInfos, 12299 components, 80 with no indexed resource (0.7%) in 0.1s
- PASS progressions: 230 tables merged in 0.0s
