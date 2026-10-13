# Safe test sessions (2026-10-12)

The harness drives the real game, and the user's own campaigns live in the same `Savegames/Story` folder as the harness's saves.
On 2026-10-12 the newest save was the user's Honour Mode run (leader "Gregor", 280 mods): `-continueGame` and the Load screen both
reach it, and a Load attempt for a test save raised a Mod Verification box listing the user's play-profile mods. Nothing was
loaded and the files were untouched, but the tooling must make that impossible, not unlikely.

## Why not a separate profile or folder swap

BG3 has no profile switch on the command line, and a folder-level sandbox (junction, renamed `Savegames`, a second profile) is unsafe:
**Steam Cloud tracks `_SAVE_Public/Savegames/Story/**` and `_PROFILE_Public/modsettings.lsx`** (see
`Steam/userdata/<id>/1086940/remotecache.vdf`), so a swapped folder would sync as deletions of the user's cloud copy. So the
safety is layered, and each layer stops the bad outcome on its own (`bg3data/safety.py`, self-test `tests/safety_selftest.py`):

| Layer | What it does |
| --- | --- |
| 1 vault | before every game launch, every save folder that is not a harness save (not `Tavizard-...`) is copied to `cache/vault/<folder>/<version>/save` (content-addressed, last 3 versions kept; 15 folders, ~122 MB today). Nothing is ever written into Savegames. |
| 2 baseline | `begin_session()` snapshots size+mtime of every protected file; `session_violations()` reports any change, addition or removal. |
| 3 watchdog | a thread tied to the harness game's process: every few seconds it checks the baseline and the loaded host's name against the protected leaders (`game.protected_leaders`, default: the leader of the HonourMode save, read from its meta). Either one kills the game at once - before an autosave - and writes `cache/vault/alerts.log`. It ends when its game exits (or never came up), so it can never act on a game the user starts later. |
| 4 box gate | a Mod Verification box that lists *Missing or Disabled* mods belongs to a campaign made with other mods, never a harness save (kept in step with the dev profile). The loader presses Start Game only on a box that has only version mismatches / new mods; a foreign or unreadable box is cancelled. |
| 5 no blind actions | `continue_guard()`: -continueGame only when the newest save is a harness save, else the game launches to the main menu and loads one by title. `load_save` selects through `select_save()`: only the harness leader's playthrough is searched, Honour saves are refused, and the game's view model must show exactly that save selected (title, time, owner; no other playthrough) before Load Game is pressed - the Load screen opens with the newest playthrough selected and its Load button loads that (the 2026-10-12 root cause). `dry_run` selects and verifies without pressing. The restart loop never presses Enter in the guarded path (the main menu's focused button is Continue). |

A launch refuses to start if the vault or watchdog can't be set up.

## Using it

- `bg3_game_restart(deploy_layer, save="wiz-evoker L20")`: deploy, launch, load that harness save (by title; the newest of identical titles).
- `bg3_safety_check()` / `("protect")` / `("verify")`: state, vault now, did any protected file change?
- Restoring is manual and deliberate: close the game, copy `cache/vault/<folder>/<newest version>/save/*` over the campaign's folder.
- Config (`layers.json` `game`): `test_leader` (default Tavizard), `protected_leaders`, `auto_accept_mod_verify` (default true, only ever for a harness-classified box).

## Known limits

- The Mod Verification box's text is read from the Noesis visual tree; if it can't be read the box is "unknown" and is cancelled, so
  an unreadable box blocks a harness load (fail safe) until the reader is fixed.
- The identity check needs Script Extender to be up; before that the baseline check still runs. A new-game character named like a
  protected leader would be killed - the new-game flow names its characters "Tavizard".
- Honour Mode autosaves on events; the watchdog polls every few seconds, so a protected campaign that somehow loaded and saved within
  that window is only recoverable from the vault.
