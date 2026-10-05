"""MCP server: layered BG3 game-data lookup (base game + additive mod layers)."""
import functools
import importlib
import json
import os
import sys
import threading
import time
import traceback

from mcp.server.mcpserver import MCPServer

from . import format as fmt
from . import index, query, se, sources

mcp = MCPServer(
    "bg3-data",
    instructions=(
        "Baldur's Gate 3 game data: the base game (read from the installed game's paks, including hotfixes) "
        "plus additive mod layers (e.g. dnd55e, apotheosis). Most tools take `layers`: omit it for every "
        "layer, or pass mod-layer names to stack on top of base (base is always included), e.g. ['dnd55e']. "
        "Resolved stats show which layer set each field. Use bg3_layers to see layers and their timestamps."
    ),
)

_store = None
_lock = threading.RLock()  # one SQLite connection shared by MCP worker threads: serialise all access
_last_check = 0.0
MAX_OUTPUT = 24_000  # characters; keeps a single answer from flooding the caller's context


# Hot reload: tool modules are re-imported when their source changes, so MCP code edits apply on the next call
# without a client reconnect. Not reloaded: the stores and connections that hold live state (query, index,
# sources, se) and this module itself - those still need a reconnect.
HOT = ("format", "lint", "icons", "iconkit", "drafting", "testing", "deploy", "groundtruth", "toolkit", "gameui", "parse", "saves", "deps", "vortex")
_mtimes = {}


def _loaded_mtime(mod):
    """Source mtime recorded in the module's .pyc header (what the loaded code was compiled from), or None."""
    try:
        with open(mod.__cached__, "rb") as f:
            head = f.read(16)
        return None if int.from_bytes(head[4:8], "little") & 1 else int.from_bytes(head[8:12], "little")
    except (OSError, AttributeError, TypeError):
        return None


def _hot_reload():
    pkg = __package__ or "bg3data"
    for name in HOT:
        mod = sys.modules.get(f"{pkg}.{name}")
        path = getattr(mod, "__file__", None)
        if not path:
            continue
        try:
            m = os.path.getmtime(path)
        except OSError:
            continue
        if name not in _mtimes:  # first sight: compare with the source mtime the loaded .pyc was compiled from
            loaded = _loaded_mtime(mod)
            _mtimes[name] = m if loaded is None or loaded == int(m) & 0xFFFFFFFF else -1.0
        if _mtimes[name] != m:
            try:
                importlib.reload(mod)
                _log(f"hot-reloaded {name}")
            except Exception:
                _log(f"hot reload of {name} failed:\n" + traceback.format_exc())
            _mtimes[name] = m


def guarded(fn):
    """Serialise, cap output, and turn exceptions into a readable message instead of a stack trace."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            with _lock:
                _hot_reload()
                out = fn(*args, **kwargs)
        except (ValueError, sources.ConfigError, FileNotFoundError) as e:
            return f"error: {e}"
        except Exception as e:
            _log(traceback.format_exc())
            return f"internal error in {fn.__name__}: {type(e).__name__}: {e}"
        if isinstance(out, str) and len(out) > MAX_OUTPUT:
            out = out[:MAX_OUTPUT] + f"\n... [truncated: {len(out) - MAX_OUTPUT} more characters; narrow the query or lower `limit`]"
        return out
    return wrapper


MAX_INPUT = 500  # SQLite rejects LIKE patterns over ~50 KB; no real BG3 name is anywhere near this


def _text(value, what="text"):
    if value is not None and len(value) > MAX_INPUT:
        raise ValueError(f"`{what}` is {len(value)} characters; the maximum is {MAX_INPUT}")
    return value


def _limit(n, default=50, hi=500):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, hi))


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def store():
    """Open the index; re-check layer signatures at most once a minute."""
    global _store, _last_check
    with _lock:
        if _store is None:
            _store = query.Store(refresh=True, log=_log)
            _last_check = time.time()
        elif time.time() - _last_check > 60:
            if index.refresh(_store.db, sources.load_config(), log=_log):
                _store.invalidate()
            _store.cfg = sources.load_config()
            _last_check = time.time()
        return _store


@mcp.tool()
@guarded
def bg3_layers() -> str:
    """List data layers in load order with source timestamps and entry counts."""
    return fmt.layers(store())


@mcp.tool()
@guarded
def bg3_refresh(force: str | None = None) -> str:
    """Re-index layers whose sources changed. force: a layer name, or 'all', to rebuild regardless."""
    s = store()
    rebuilt = index.refresh(s.db, sources.load_config(), force=[force] if force and force != "all" else force, log=_log)
    s.invalidate()
    return f"rebuilt: {rebuilt or 'nothing (all up to date)'}\n\n" + fmt.layers(s)


@mcp.tool()
@guarded
def bg3_add_mod_layer(name: str, path: str, position: int | None = None) -> str:
    """Register a mod layer: an unpacked mod folder (containing Public/ and Mods/) or a .pak file.
    position: 1-based slot among mod layers (default: on top). The layer is indexed immediately."""
    cfg = sources.load_config()
    if any(m["name"] == name for m in cfg["mods"]):
        return f"layer '{name}' already exists"
    mod = {"name": name, "path": path}
    if position is None:
        cfg["mods"].append(mod)
    else:
        cfg["mods"].insert(max(0, position - 1), mod)
    import os
    if not (os.path.isdir(path) or (path.lower().endswith(".pak") and os.path.isfile(path))):
        return f"error: {path!r} is neither a mod folder nor a .pak file"
    sources.save_config(cfg)
    s = store()
    index.refresh(s.db, cfg, log=_log)
    s.cfg = cfg
    s.invalidate()
    return fmt.layers(s)


@mcp.tool()
@guarded
def bg3_remove_mod_layer(name: str) -> str:
    """Unregister a mod layer (base cannot be removed)."""
    cfg = sources.load_config()
    before = len(cfg["mods"])
    cfg["mods"] = [m for m in cfg["mods"] if m["name"] != name]
    if len(cfg["mods"]) == before:
        return f"no mod layer '{name}'"
    sources.save_config(cfg)
    s = store()
    index.refresh(s.db, cfg, log=_log)
    s.invalidate()
    return fmt.layers(s)


@mcp.tool()
@guarded
def bg3_get_entry(name: str, layers: list[str] | None = None, provenance: bool = True) -> str:
    """Resolve a stats entry (spell, status, passive, character, object, weapon, ...) through inheritance
    across the selected layers. Shows the `using` chain and, per field, the layer/source that set it."""
    _text(name, "name")
    s = store()
    return fmt.entry(s, name, s.active(layers), provenance)


@mcp.tool()
@guarded
def bg3_search(text: str, type: str | None = None, field: str | None = None,
               layers: list[str] | None = None, defined_in: str | None = None, limit: int = 50) -> str:
    """Find stats entries whose name or any field contains `text`. type: SpellData, StatusData,
    PassiveData, Character, Object, Weapon, Armor, InterruptData... field: restrict to one field.
    defined_in: only entries that this layer itself defines or overrides (e.g. 'dnd55e' = what dnd55e adds)."""
    if not text or not text.strip():
        return "error: `text` must not be empty"
    _text(text); _text(field, "field")
    s = store()
    active = s.active(layers)
    if defined_in:
        if defined_in not in active:
            active = s.active((layers or []) + [defined_in]) if defined_in != "base" else active
        rows = s.search_stats(text, [defined_in], type, field, _limit(limit))
        return "\n".join(f"{n} ({t}) [{l}]" for n, t, l in rows) or f"no matches defined in {defined_in}"
    rows = s.search_stats(text, active, type, field, _limit(limit))
    return "\n".join(f"{n} ({t}) [{l}]" for n, t, l in rows) or "no matches"


@mcp.tool()
@guarded
def bg3_references(token: str, layers: list[str] | None = None, limit: int = 100) -> str:
    """Everything that mentions `token` (entry name, status, passive, GUID...) in stats, templates,
    progressions and lists."""
    if not token or len(token.strip()) < 3:
        return "error: `token` needs at least 3 characters"
    _text(token, "token")
    s = store()
    rows = s.references(token, s.active(layers), _limit(limit, 100))
    return "\n".join(f"{t:9s} {n} [{src}]" for t, n, l, src in rows) or "no references"


@mcp.tool()
@guarded
def bg3_diff(name: str, layer: str, layers: list[str] | None = None) -> str:
    """What `layer` changes about stats entry NAME compared with the layers below it."""
    s = store()
    active = s.active(layers)
    if layer not in active:
        return f"layer '{layer}' is not in the active stack {active}"
    return fmt.diff(s, name, layer, active)


@mcp.tool()
@guarded
def bg3_loca(query: str, layers: list[str] | None = None, limit: int = 30) -> str:
    """Look up a localization handle (e.g. h1a950eaag...), or search localized text."""
    _text(query, "query")
    s = store()
    active = s.active(layers)
    q = query.strip()
    if " " not in q and q.startswith("h") and len(q.split(";")[0]) >= 20:
        r = s.loca(q, active)
        return f"{r[0]}    [{r[1]}, v{r[2]}]" if r else "handle not found"
    rows = s.loca_search(q, active, _limit(limit, 30))
    return "\n".join(f"{h}  {t[:160]}  [{src}]" for h, t, src in rows) or "no matches"


@mcp.tool()
@guarded
def bg3_template(key: str, layers: list[str] | None = None) -> str:
    """Resolve a root template (MapKey GUID or Name) through its ParentTemplateId chain."""
    _text(key, "key")
    s = store()
    return fmt.template(s, key, s.active(layers))


@mcp.tool()
@guarded
def bg3_progression(key: str, level: int | None = None, layers: list[str] | None = None) -> str:
    """Progression nodes for a class/subclass (Name, e.g. 'Soulknife', or TableUUID), merged by node UUID
    so a higher layer's node replaces a lower one's."""
    s = store()
    return fmt.progression(s, key, s.active(layers), level)


@mcp.tool()
@guarded
def bg3_static(kind: str, key: str, layers: list[str] | None = None) -> str:
    """Static game data by UUID or Name (highest layer wins): kind = ClassDescription (classes/subclasses,
    with ProgressionTableUUID), LevelMapSeries (e.g. SuperiorityDie, per-level values), ActionResourceDefinition
    (resources: MaxValue, ReplenishType), Feat. A partial key lists matches."""
    kinds = ("ClassDescription", "LevelMapSeries", "ActionResourceDefinition", "Feat")
    if kind not in kinds:
        return f"error: kind must be one of {kinds}"
    _text(key, "key")
    s = store()
    active = s.active(layers)
    r = s.static(kind, key, active)
    if r:
        layer, src, a = r
        return f"{kind} {a.get('Name')} {a.get('UUID')} [{src}]\n" + "\n".join(f"  {k} = {v}" for k, v in a.items() if k not in ("Name", "UUID"))
    rows = s.static_search(kind, key, active)
    return "\n".join(f"{n}  {u}  [{l}]" for n, u, l in rows) or f"no {kind} matching '{key}'"


@mcp.tool()
@guarded
def bg3_spell_list(key: str, layers: list[str] | None = None) -> str:
    """A spell/passive/skill list by UUID or Name (highest layer wins), plus the lists the game merges into it at load
    (MergedInto) - what a level-up actually offers from it."""
    s = store()
    act = s.active(layers)
    r = s.spell_list(key, act)
    if not r:
        return "list not found"
    node, uuid, name, src, attrs = r
    a = json.loads(attrs)
    out = f"{node} {name} {uuid} [{src}]\n" + "\n".join(f"  {k} = {v}" for k, v in a.items() if k not in ("UUID", "Name"))
    merged = s.merged_into(uuid, act)
    if merged:
        full = s.runtime_list_spells(uuid, act) or []
        out += (f"\n  in game: {len(full)} spells - merged in at load (MergedInto): "
                + ", ".join(f"{n} ({u[:8]}, {len(sp)})" for u, n, sp in merged))
    return out


@mcp.tool()
@guarded
def bg3_spell_visuals(name: str, layers: list[str] | None = None) -> str:
    """The visual/audio kit of a spell: effects, animations, sounds, icon - for reusing vanilla VFX."""
    s = store()
    active = s.active(layers)
    r = s.resolve(name, active)
    if not r:
        return f"'{name}' not found"
    kit = {k: (v, src) for k, (v, src) in r["fields"].items() if query.VISUAL_KEYS.search(k) and v}
    lines = [f"{name} - \"{s.display_name(r['fields'], active)}\""]
    for k, (v, src) in sorted(kit.items()):
        lines.append(f"  {k} = {v}    [{src}]")
        if "Effect" in k:
            for g in query.GUID.findall(v):
                e = s.effect(g, active)
                if e:
                    comps = ", ".join(c["name"] or "?" for c in e.get("effects", [])) if e["kind"] == "MultiEffectInfo" else e["file"]
                    lines.append(f"      {g} = {e['name']}" + (f"  [{comps}]" if comps else ""))
    return "\n".join(lines)


@mcp.tool()
@guarded
def bg3_similar_spells(damage_type: str | None = None, school: str | None = None, spell_type: str | None = None,
                       level: int | None = None, keyword: str | None = None,
                       layers: list[str] | None = None, limit: int = 25) -> str:
    """Spells matching damage type / school / spell type (Target, Projectile, Shout, Zone...) / level /
    name keyword, with their visual kits - to borrow effects and animations for new spells."""
    s = store()
    rows = s.similar_spells(s.active(layers), damage_type, school, spell_type, level, keyword, _limit(limit, 25, 100))
    out = []
    for n, dn, lvl, sch, kit in rows:
        out.append(f"{n} - \"{dn}\" L{lvl} {sch}")
        for k in sorted(kit):
            if "Effect" in k or "Animation" in k or "Sound" in k:
                out.append(f"    {k} = {kit[k][:120]}")
    return "\n".join(out) or "no matches"


@mcp.tool()
@guarded
def bg3_effect(guid: str, layers: list[str] | None = None) -> str:
    """Resolve an effect GUID: a MultiEffectInfo (the value of stats *Effect fields) with its component
    effect resources (names, bones, duration, looping, .lsfx file), or a single effect resource.
    Also lists the stats entries that use it."""
    g = (guid or "").strip().lower()
    if not query.GUID.fullmatch(g):
        return "error: expected a GUID like 4cab2089-4c14-44f6-9fe0-5421ec911552"
    s = store()
    return fmt.effect(s, g, s.active(layers))


@mcp.tool()
@guarded
def bg3_search_effects(text: str, layers: list[str] | None = None, limit: int = 20) -> str:
    """Find effects by what they're called (e.g. 'Necrotic', 'Impact_BodyFX', 'Radiant_Beam'):
    MultiEffectInfos whose name, or any component effect's name, matches. Returns the GUID to put in a
    stats *Effect field, what matched, and sample spells/statuses already using it."""
    if not text or len(text.strip()) < 3:
        return "error: `text` needs at least 3 characters"
    _text(text)
    s = store()
    rows = s.search_effects(text.strip(), s.active(layers), _limit(limit, 20, 100))
    out = []
    for uuid, name, source, why, users in rows:
        out.append(f"{uuid}  {name}  [{source}, matched {why}]" + (("\n    used by: " + ", ".join(n for n, _ in users)) if users else ""))
    return "\n".join(out) or "no matching effects"


# ---------------------------------------------------------------------------- Script Extender
# These talk to the RUNNING game through the SE console (bundled bg3data/ps/se_inject.ps1).
# They don't take the index lock, so data lookups stay responsive while the game is busy.

def se_guarded(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            with _lock:  # live-game tools hot-reload too (their calls aren't serialised beyond this)
                _hot_reload()
            out = fn(*args, **kwargs)
        except (RuntimeError, TimeoutError, ValueError, sources.ConfigError) as e:
            return f"error: {e}"
        except Exception as e:
            _log(traceback.format_exc())
            return f"internal error in {fn.__name__}: {type(e).__name__}: {e}"
        if isinstance(out, str) and len(out) > MAX_OUTPUT:
            out = out[:MAX_OUTPUT] + f"\n... [truncated: {len(out) - MAX_OUTPUT} more characters]"
        return out
    return wrapper


@mcp.tool()
@se_guarded
def bg3_se_status() -> str:
    """Is the game running, which Script Extender log belongs to this run, and the latest game state."""
    from . import deploy
    return "\n".join([se.status()] + deploy.deployed_lines(store()))


@mcp.tool()
@se_guarded
def bg3_se_eval(code: str, context: str = "server", timeout: float = 15, delay: float = 0) -> str:
    """Run Lua inside the RUNNING game via Script Extender and return its printed output plus the return
    value (JSON). context: 'server' (game logic, Osi.*, Ext.Stats) or 'client' (UI/visuals).
    delay: seconds to wait first (e.g. for a cast or boost to resolve). The test harness, once staged,
    is available as the global BG3T. This executes code in the user's live game session."""
    if not code or len(code) > 8000:
        return "error: `code` must be 1-8000 characters"
    if delay:
        time.sleep(max(0.0, min(float(delay), 30.0)))
    r = se.eval_lua(code, context, timeout=max(2.0, min(float(timeout), 120.0)))
    out = [("OK" if r["ok"] else "LUA ERROR") + ": " + json.dumps(r["result"], indent=1)[:MAX_OUTPUT // 2]]
    if r["output"]:
        out.append("printed:\n" + "\n".join(r["output"]))
    return "\n".join(out)


@mcp.tool()
@se_guarded
def bg3_se_command(line: str, wait: float = 3, context: str = "server") -> str:
    """Send one Script Extender console line to the running game (e.g. '!apofeature Barbarian_PersistentRage')
    and return the log lines it produced within `wait` seconds (max 60)."""
    if not line or len(line) > 2000:
        return "error: `line` must be 1-2000 characters"
    lines = se.command(line, wait=float(wait), context=context)
    return "\n".join(lines) or "(no log output)"


@mcp.tool()
@se_guarded
def bg3_se_log(filter: str | None = None, lines: int = 60) -> str:
    """Tail the current game run's Script Extender runtime log, optionally only lines containing `filter`
    (e.g. '[Apotheosis]', 'error', 'FAILED')."""
    name, text = se.log_tail(filter, _limit(lines, 60, 1000))
    return f"{name}:\n" + ("\n".join(text) or "(no matching lines)")


@mcp.tool()
@se_guarded
def bg3_se_live_entry(name: str, layers: list[str] | None = None, fields: list[str] | None = None) -> str:
    """Ground truth: compare a stats entry as the running game actually loaded it with the index's
    resolution for `layers`. Pick layers matching what's deployed. Shows each field as MATCH or DIFF."""
    _text(name, "name")
    with _lock:
        s = store()
        active = s.active(layers)
        r = s.resolve(name, active)
    if not r and not fields:
        return f"'{name}' not in the index for layers {active}; pass `fields` to read it from the game anyway"
    want = fields or sorted(k for k in r["fields"] if k not in ("SpellType", "StatusType"))
    live = se.live_stats_many({name: want}).get(name)
    if live is None:
        return f"'{name}' is not loaded in the running game"
    out = [f"{name}: game vs index ({'+'.join(active)})"]
    diff = 0
    for k in want:
        iv = r["fields"].get(k, (None, ""))[0] if r else None
        gv = live.get(k)
        if not se.comparable(gv):
            out.append(f"  n/a   {k}: index={iv!r}  (game exposes parsed functors; not comparable as text)")
            continue
        same = se.same(iv, gv)
        diff += not same
        out.append(f"  {'MATCH' if same else 'DIFF '} {k}: index={iv!r}" + ("" if same else f"  game={gv!r}")
                   + (f"  [{r['fields'][k][1]}]" if r and k in r["fields"] else ""))
    out.insert(1, f"  {len(want) - diff}/{len(want)} fields match")
    return "\n".join(out)


@mcp.tool()
@se_guarded
def bg3_se_hot_load(layer: str, files: list[str] | None = None, loca: bool = True) -> str:
    """HOT-LOAD a mod layer into the RUNNING game without restarting: mirrors its stats .txt files as loose
    files under <game>/Data/Public/BG3DataHot_<layer>/, loads them with Ext.Stats.LoadStatsFile(…, true)
    (overwriting existing entries), Syncs every entry to the client, and (loca=True) pushes its English
    strings. files: only these stats files (e.g. ['Passive.txt']). Progressions/lists/templates can't be
    hot-loaded (restart with a pak). Test-only: don't save the game while relying on hot-loaded entries.
    Clean up with bg3_se_hot_clean."""
    r = se.hot_load_stats(layer, files)
    out = [f"hot-loaded {layer}: {r['synced']} entries synced ({r['created_new']} newly created in this session)"]
    for f in r["files"]:
        out.append(f"  {'OK ' if f['ok'] else 'ERR'} {f['file'].split('/')[-1]}: {f['entries']} entries" + (f"  {f['err']}" if f.get("err") else ""))
    if r["sync_errors"]:
        out.append("sync errors: " + "; ".join(r["sync_errors"]))
    if r["engine_errors"]:
        out.append("engine messages: " + "; ".join(r["engine_errors"]))
    if loca:
        l = se.hot_load_loca(layer)
        out.append(f"loca: {l.get('updated', 0)}/{l['strings_sent']} strings updated" + (f", {l['failed']} failed" if l.get("failed") else ""))
    out.append(f"loose files: {r['hot_root']} (remove with bg3_se_hot_clean)")
    return "\n".join(out)


@mcp.tool()
@se_guarded
def bg3_se_hot_clean() -> str:
    """Remove the loose hot-load folders (Data/Public/BG3DataHot_*) from the game install. Entries already
    hot-loaded stay in the running session until restart."""
    removed = se.hot_clean()
    return "removed: " + (", ".join(removed) if removed else "nothing")


@mcp.tool()
@se_guarded
def bg3_se_reset_lua() -> str:
    """Reload Script Extender Lua (console `reset`): re-runs every loaded mod's Bootstrap scripts from its
    pak/loose files. Only affects mods that were loaded at game start."""
    lines = se.command("reset", wait=6)
    return "\n".join(lines[-40:]) or "(no log output)"


# ---------------------------------------------------------------------------- in-game testing
# Mod-agnostic test loop: level up by XP, check the level against progressions, and run TOML test cases
# (<mod>/tests/bg3/*.toml) as real encounters. A scripted cast never pays costs, so only mode="player"
# cases (the user casts from the hotbar) can verify slots/resources; every verdict states its fidelity.

def _testing_store(layers):
    with _lock:
        s = store()
        return s, s.active(layers)


@mcp.tool()
@guarded
def bg3_environment() -> str:
    """What this machine looks like to bg3-data: Windows or WSL, the discovered game install (Steam library,
    GOG or configured), game exe, Divine.exe, the Larian user folder (Mods, modsettings.lsx, Script Extender
    logs), Script Extender presence, detected mod managers (Vortex, BG3 Mod Manager, in-game) and what they
    mean for hand-deployed paks, and each mod layer's deploy/enable state."""
    from . import deploy
    return deploy.environment()


@mcp.tool()
@se_guarded
def bg3_deploy(layer: str, enable: bool = True) -> str:
    """Pack a mod layer (Mods/<folder> + Public/<folder>, via Divine), deploy the pak to the user Mods folder
    (refused while the game runs; previous pak backed up) and enable it in the active profile's
    modsettings.lsx after its dependencies. Works for any mod; no per-mod scripts. Re-run after a mod
    manager rewrites the load order."""
    from . import deploy
    lines, ok = deploy.deploy(layer, enable)
    return "\n".join(lines)


@mcp.tool()
@se_guarded
def bg3_game_restart(deploy_layer: str | None = None, launch: bool = True) -> str:
    """Kill the game, optionally deploy a mod layer (its layers.json `deploy` command, else the built-in
    bg3_deploy), relaunch (Steam -applaunch, or the game exe for GOG/other installs; --skip-launcher
    -continueGame loads the NEWEST save) and wait until a host character is loaded. Unsaved progress is lost."""
    from . import testing
    return testing.restart(deploy_layer, launch)


@mcp.tool()
@se_guarded
def bg3_level_up(levels: int = 1, layers: list[str] | None = None) -> str:
    """Grant the host exactly enough XP (from the layers' XPData) to reach `levels` more levels. The user
    then levels up in the UI, so every feature and spell is class-sourced; follow with bg3_level_check."""
    from . import testing
    s, active = _testing_store(layers)
    r = testing.grant_levels(s, active, max(1, min(int(levels), 19)))
    if not r["granted"]:
        return f"level {r['level']}, XP {r['xp']}: {r['note']}"
    return (f"level {r['level']} -> {r['target']}: granted {r['granted']} XP (total {r.get('xp_after')}, needed {r['needed_total']}). "
            "Open the level-up screen in game, then run bg3_level_check.")


@mcp.tool()
@se_guarded
def bg3_level_check(layers: list[str] | None = None) -> str:
    """Compare the host with its class/subclass progressions up to its current level: passives added and
    removed, AddSpells lists (with the spell's source), ActionResource boosts, and this level's choices."""
    from . import testing
    s, active = _testing_store(layers)
    return testing.level_check(s, active)


@mcp.tool()
@se_guarded
def bg3_test_list(layer: str, class_name: str | None = None, level: int | None = None, layers: list[str] | None = None) -> str:
    """Test cases a mod layer defines (TOML under <mod>/tests/bg3, or layers.json `tests`), validated
    against the index (spells, statuses, templates, aliases)."""
    from . import testing
    s, active = _testing_store(layers)
    return testing.list_cases(s, active, layer, class_name, level)


@mcp.tool()
@se_guarded
def bg3_icon_check(layer: str, layers: list[str] | None = None) -> str:
    """Ask the RUNNING game which of a layer's stats icons exist (client Ext.StaticData.GetIconUVs) - a missing
    icon shows blank in game. Caches the answers so bg3_lint_stats reports missing icons offline afterwards."""
    from . import icons
    s = store()
    return icons.check(s, layer, s.active(layers))


def _native(path):
    from . import platform
    return platform.to_native(path)


@mcp.tool()
@guarded
def bg3_icon_extract(names: list[str], out_dir: str) -> str:
    """Base-game icons as PNG (their 380 px tooltip versions) into out_dir (a Linux or Windows path), to use as
    reference or as source art. A name may be an exact icon name or a substring (up to 12 matches each)."""
    from . import iconkit
    out, missing, n = iconkit.extract(store(), names, _native(out_dir))
    return "\n".join([f"{len(out)} PNG(s) from {n} base-game tooltip icons:"] + out + ([f"not found: {', '.join(missing)}"] if missing else []))


@mcp.tool()
@guarded
def bg3_icon_build(layer: str, src_dir: str | None = None, atlas: str = "Icons", thick: int = 7) -> str:
    """Build a mod's own icons from <src_dir>/<IconName>.png (default <mod root>/Icons/src): the hotbar atlas (.dds
    with mips + GUI/<Atlas>.lsx + its TextureBank resource), 380/192 px tooltip and 144/72 px controller DDS per
    icon, and GUI/metadata.lsf. <IconName> is the stats `Icon` value. Then redeploy (bg3_game_restart) and
    bg3_icon_check. Files starting with _ are skipped."""
    from . import iconkit
    s = store()
    if not src_dir:
        root, _ = iconkit.mod_dirs(s, layer)
        src_dir = os.path.join(root, "Icons", "src")
    return iconkit.build(s, layer, _native(src_dir), atlas, thick)


@mcp.tool()
@guarded
def bg3_icon_import(paths: list[str], layer: str, names: list[str] | None = None, key: str = "unmix",
                    tints: list[str] | None = None, crop: bool = False) -> str:
    """Take generated images (e.g. ComfyUI outputs) into a mod's icon sources (<mod root>/Icons/src) as
    <IconName>.png: the green screen the BG3 icon LoRAs paint on is unmixed (key="unmix": the soft glow over it survives
    as semi-transparent haze, like base-game icons; "green" = hard key; "none" to skip), centre-cropped,
    512 px. Black-background art (SDXL + IP-Adapter): key="black", or tints=[fire|cold|lightning|thunder|acid|poison|
    necrotic|radiant|psychic|force|healing|arcane|earth per path] to recolour to that damage type's base-game gradient;
    crop=True fills the tile. names: one icon name per path (default: the file name without ComfyUI's _00001_ counter).
    Then bg3_icon_build."""
    from . import iconkit
    s = store()
    root, _ = iconkit.mod_dirs(s, layer)
    out = iconkit.import_art([_native(p) for p in paths], os.path.join(root, "Icons", "src"), names, key, tints=tints, crop=crop)
    return "\n".join([f"{len(out)} icon source(s):"] + out)


@mcp.tool()
@guarded
def bg3_icon_plate(layer: str) -> str:
    """Rebuild the stone plate base-game hotbar spell tiles are painted on (from the game's own skill atlas) into
    <mod root>/Icons/hotbar_plate.png. With it, bg3_icon_build makes base-game-style hotbar tiles (plate, stroke shadow,
    warm yellow halo); the tooltip icons stay a bare glow."""
    from . import iconkit
    s = store()
    root, _ = iconkit.mod_dirs(s, layer)
    path, n = iconkit.make_plate(s, os.path.join(root, "Icons", "hotbar_plate.png"))
    return f"plate from {n} base hotbar spell tiles -> {path}"


@mcp.tool()
@guarded
def bg3_icon_resources(layer: str, mapping: dict[str, str] | None = None) -> str:
    """Action resource icons (a resource without one shows a red dot in the resource bar): {resource name: icon} where icon
    is an Icons/src name (Apo_...) or a base-game icon name; default: <mod root>/Icons/resource_icons.json. Writes the four
    48 px pip states (normal, highlight, missing, used), the 80 px controller icon and low-res copies, and the metadata."""
    from . import iconkit
    import json as _json, tempfile
    s = store()
    root, _ = iconkit.mod_dirs(s, layer)
    mapping = mapping or _json.load(open(os.path.join(root, "Icons", "resource_icons.json"), encoding="utf-8"))
    tmp = tempfile.mkdtemp()
    base = sorted({v for v in mapping.values() if not os.path.exists(os.path.join(root, "Icons", "src", f"{v}.png"))})
    iconkit.extract(s, base, tmp) if base else None
    paths = {}
    for res, ic in mapping.items():
        for p in (os.path.join(root, "Icons", "src", f"{ic}.png"), os.path.join(tmp, f"{ic}.png")):
            if os.path.exists(p):
                paths[res] = p
                break
    missing = sorted(set(mapping) - set(paths))
    return iconkit.build_resources(s, layer, paths) + (f"\n  no symbol for: {', '.join(missing)}" if missing else "")


@mcp.tool()
@guarded
def bg3_icon_preview(src_dir: str, size: int = 128) -> str:
    """Contact sheet (PNG) of <src_dir>/*.png with names, to review a batch of icons; returns its path (open it
    with an image-capable Read)."""
    from . import iconkit
    path, n = iconkit.preview(_native(src_dir), size=size)
    return f"{n} icons -> {path}"


@mcp.tool()
@guarded
def bg3_test_draft(passives: list[str] | None = None, key: str | None = None, level: int | None = None,
                   layers: list[str] | None = None) -> str:
    """Draft test cases (TOML) for features from their stats: passives by name, or every passive a class/subclass
    progression (key = Name or TableUUID) adds at `level`. Unlocked spells are cast at a fitting target with
    saves forced to fail / attacks forced to hit, interrupts get the attack or roll that triggers them, rest and
    initiative functors get matching staging, resistances get a damage check plus a non-overlapping -control.
    Resources the feature spends but doesn't grant are set up; reactions are opted in. Each draft carries a
    confidence and TODOs; review, then save under <mod>/tests/bg3/ and run with bg3_test_run."""
    from . import drafting
    s, active = _testing_store(layers)
    return drafting.drafts(s, active, passives, key, level)


@mcp.tool()
def bg3_test_spell_check(spell: str, layers: list[str] | None = None) -> str:
    """Pre-check a spell before writing a test: can the AI cast it (AIFlags), is its save the spell's OWN roll
    (the only kind OnPostRoll interrupts see) or a status/surface/passive save, requirements, target filter,
    costs. Catches the dead ends of AI-driven tests before a single run."""
    from . import testing
    s, active = _testing_store(layers)
    return testing.spell_check(s, active, spell)


@mcp.tool()
def bg3_save_spells(ability: str, layers: list[str] | None = None, limit: int = 40) -> str:
    """AI-castable spells whose own roll is a saving throw of `ability` (e.g. 'Strength'): the spells an
    enemy can be locked to (mode ai) to test save modifiers, floors and save interrupts for real."""
    from . import testing
    s, active = _testing_store(layers)
    return testing.save_spells(s, active, ability, limit)


@mcp.tool()
@se_guarded
def bg3_test_script(layer: str, class_name: str | None = None, level: int | None = None, layers: list[str] | None = None) -> str:
    """Write the human test script (step-by-step reading script per level and case) to
    <mod>/docs/test-scripts/ and return it."""
    from . import testing
    s, active = _testing_store(layers)
    text, path = testing.script(s, active, layer, class_name, level)
    return (f"written to {path}\n\n" if path else "") + text


@mcp.tool()
@se_guarded
def bg3_test_stage(layer: str, case_id: str, layers: list[str] | None = None) -> str:
    """Set up one test case in the running game: preconditions (class level, spell learned through the
    class, not in combat), spawns, HP/status setup, host-first initiative, safety watch, event recorder.
    mode="script" cases are cast immediately. Then the user acts; finish with bg3_test_verify."""
    from . import testing
    s, active = _testing_store(layers)
    r = testing.stage(s, active, layer, case_id)
    if not r["ok"]:
        return "NOT STAGED - blockers:\n" + "\n".join(f"  - {b}" for b in r["blockers"]) + \
               ("\nnotes:\n" + "\n".join(f"  - {n}" for n in r["notes"]) if r["notes"] else "")
    c = r["case"]
    out = [f"staged {c['id']}: {c.get('title', '')}"]
    out += [f"  note: {n}" for n in r["notes"]]
    if r["combat"]:
        ft = r["first_turn"]
        out.append("  combat: " + ("your turn first" if ft and ft.get("tracked") == "host" else
                                   f"first turn went to {ft.get('tracked') or 'someone else'}" if ft else "no turn recorded yet"))
    for alias, snap in (r["before"] or {}).items():
        out.append(f"  {alias}: HP {snap.get('hp')}/{snap.get('max_hp')}" + (f", statuses {', '.join(snap.get('statuses') or [])}" if snap.get("statuses") else ""))
    if c.get("mode", "auto") == "player" and r["spell_name"]:
        tgt = "yourself" if c.get("target", "host") == "host" else c["target"]
        out.append(f"USER: cast {r['spell_name']} from the class spell bar at {tgt}, then say verify")
    else:
        out.append("scripted cast sent; run bg3_test_verify (or use bg3_test_run next time)")
    return "\n".join(out)


@mcp.tool()
@se_guarded
def bg3_test_verify(cleanup: bool = True, wait: float = 2.0, layers: list[str] | None = None) -> str:
    """Evaluate the staged case's expectations (before/after snapshots + recorded events), report
    PASS/FAIL per check with its fidelity, then remove every spawn, test boost and applied status."""
    from . import testing
    s, active = _testing_store(layers)
    return testing.verify(s, active, cleanup, wait)


@mcp.tool()
@se_guarded
def bg3_test_run(layer: str, case_id: str, wait: float = 4.0, layers: list[str] | None = None) -> str:
    """Run one auto/script case end to end: stage (real combat, host acts first), scripted cast, verify,
    cleanup. Effects come from recorded events; costs are checked against the spell's loaded UseCosts
    (a scripted cast never charges them). player-mode cases need bg3_test_stage + a hotbar cast instead."""
    from . import testing
    s, active = _testing_store(layers)
    return testing.run(s, active, layer, case_id, wait)


@mcp.tool()
@se_guarded
def bg3_test_run_level(layer: str, level: int, build: str | None = None, class_name: str | None = None, wait: float = 4.0,
                       layers: list[str] | None = None) -> str:
    """Run every automated case for a level, one after another, and summarise: the cases assigned to
    `build` (see bg3_test_plan), or all of `class_name`'s cases at that level. Lists the player-mode cases
    that still need a hotbar cast."""
    from . import testing
    s, active = _testing_store(layers)
    return testing.run_level(s, active, layer, class_name, level, wait, build)


@mcp.tool()
@guarded
def bg3_save_game(name: str) -> str:
    """Save the running game under a name (pause menu -> Save Game -> New Save, the name typed in). Waits until the save exists."""
    from . import gameui
    ok, info = gameui.save_game(name)
    return f"saved: {info}" if ok else "not saved: " + info


@mcp.tool()
@guarded
def bg3_new_character(cls: str, save_as: str | None = None) -> str:
    """A level 1 character of any class from the current host, through the game's respec (Osi.StartRespec - what Withers does):
    the class's choices are pre-filled by the game; race, background, name and XP stay. Load a low-level save first (the XP carries
    over: from "Tavizard L2 Base" the new character is level 1 with XP for level 2). save_as: save it under that name (e.g.
    "Barbarian L1 Base") as a start save for test builds."""
    from . import gameui
    ok, msg = gameui.respec(cls)
    if not ok:
        return "respec failed: " + msg
    if save_as:
        ok2, info = gameui.save_game(save_as)
        return f"{msg}; " + (f"saved as {info}" if ok2 else "save failed: " + info)
    return msg


@mcp.tool()
@se_guarded
def bg3_test_build(layer: str, build: str, to_level: int | None = None, wait: float = 4.0, layers: list[str] | None = None,
                   background: bool = False, start_level: int | None = None) -> str:
    """Run a test build ([[build]] in the suite TOML) hands-free from the host's current level: every level is granted and taken with
    the automatic level-up driver using the build's plan (bg3_test_plan) - its subclass and the spells its tests need, learned
    through the level-up screen so they're class-sourced - then validated (level +1, subclass, wanted spells, level_check) and that
    level's automated tests are run. A build with `from` loads its start save by name when the host isn't that character. to_level
    stops early (~20-25 s per level plus tests). Stops at the first failure. background=True: `build` may be several ids
    separated by commas; they run one after another in a detached process (a full build is ~10 min) - read progress with
    bg3_test_build_status (lines appear as they happen). Every 5th level is saved as "<build id> L<n>"; start_level (the first
    level to re-take) loads the newest such checkpoint below it instead of levelling from the start save - use it only when
    nothing at or below that checkpoint level changed since the checkpoint was made."""
    from . import testing
    if background:
        import subprocess, sys
        log = os.path.join(sources.CACHE, "test_builds.log")
        ids = [x.strip() for x in build.split(",") if x.strip()]
        opts = (["--to", str(to_level)] if to_level else []) + (["--start", str(start_level)] if start_level else [])
        subprocess.Popen([sys.executable, "-m", "bg3data.runbuilds", log, layer] + opts + ids, cwd=os.path.dirname(os.path.dirname(__file__)),
                         stdout=subprocess.DEVNULL, stderr=open(log + ".err", "a"), start_new_session=True)
        return f"started {len(ids)} build(s) in the background; progress: bg3_test_build_status (log {log})"
    s, active = _testing_store(layers)
    return testing.run_build(s, active, layer, build, to_level, wait, start_level=start_level)


@mcp.tool()
@guarded
def bg3_test_build_status(lines: int = 60) -> str:
    """The background test-build log (bg3_test_build(background=True)): the last `lines` lines."""
    log = os.path.join(sources.CACHE, "test_builds.log")
    if not os.path.exists(log):
        return "no background build has run"
    return "\n".join(open(log, encoding="utf-8").read().splitlines()[-lines:])


@mcp.tool()
@guarded
def bg3_lint_stats(layer: str, layers: list[str] | None = None, limit: int = 200) -> str:
    """Static stats lint for a mod layer, before the game ever loads it: enum values (Cooldown,
    StatsFunctorContext, RemoveEvents, SpellFlags, TickType...) and functor/condition/boost names that no
    other layer uses (the engine silently drops what it doesn't know), plus missing referenced entries
    (statuses, unlocked spells/interrupts, using, containers), unknown action resources, and root templates
    (the layer's templates' parents/Stats/SkillList, and templates its stats Summon/Spawn)."""
    from . import lint
    s = store()
    return lint.lint_stats(s, s.active(layers), layer, _limit(limit, 200, 2000))


@mcp.tool()
@guarded
def bg3_vortex_status(log_lines: int = 12) -> str:
    """Vortex, read-only: what it deployed into the game's Mods folder (its deployment manifest), other paks there,
    staged mods (flags several versions of one mod) and its recent install/remove/deploy/purge activity for BG3.
    Answers "why isn't mod X in game" when Vortex manages it."""
    from . import vortex
    return vortex.status(log_n=max(1, min(int(log_lines), 60)))


@mcp.tool()
@guarded
def bg3_vortex_bridge_install() -> str:
    """OPTIONAL, for Vortex users: install the bg3-data-mcp bridge extension into Vortex's plugins folder so
    bg3_vortex can deploy / purge / enable / disable / remove mods through Vortex itself. Loads on Vortex's next
    start; listens on 127.0.0.1 only and requires a token stored beside it."""
    from . import vortex
    return vortex.install_bridge()


@mcp.tool()
@guarded
def bg3_vortex(action: str = "status", mod: str | None = None) -> str:
    """Drive Vortex through the bridge extension (bg3_vortex_bridge_install): action = status | deploy | purge |
    enable | disable | remove; mod = a Vortex mod id or a unique part of it (e.g. '4.11.23.0'). Acts on Vortex's
    active game/profile. remove deletes the staged mod (Vortex may ask to confirm in its window)."""
    from . import vortex
    return vortex.format_result(vortex.action(action, mod))


@mcp.tool()
@guarded
def bg3_saves(limit: int = 5) -> str:
    """Newest savegames and the mods each was made with (meta.lsf), compared with the current load order:
    mods no longer loaded, and renamed/updated ones (same UUID, different Folder/Name/Version64/MD5) - the usual
    reason a save shows a mods dialog or won't load after a mod update or rename."""
    from . import saves
    return saves.describe(sources.load_config(), max(1, min(int(limit), 30)))


@mcp.tool()
@guarded
def bg3_save_fix_mods(save: str = "latest", remove: list[str] | None = None, sync: bool = True,
                      apply: bool = False) -> str:
    """Rewrite a save's mod list: sync=True updates entries whose UUID is in the current load order (Folder, Name,
    Version64, MD5 - e.g. after a rename or update); remove drops mods by name/folder/UUID (safe for UI-only mods;
    dropping content mods can break a save). save = 'latest' or part of the save folder name. Dry run unless apply;
    the original .lsv is backed up to the MCP cache first. Close the game before applying."""
    from . import deploy, saves
    if apply and deploy.game_running():
        return "close the game first (bg3_game_restart launch=False): it may rewrite or hold the save"
    return saves.fix_mods(sources.load_config(), save, remove or [], sync, apply)


@mcp.tool()
@se_guarded
def bg3_game_dialog(dismiss: bool = True) -> str:
    """The open in-game message box, if any: its id, text and number of actions. dismiss=True clears an
    acknowledge-only box (one action, e.g. a mod/save warning) with the action plus a real Enter press; a box that
    asks a question (several actions) is reported and left for the user. bg3_game_restart and test runs do this
    automatically."""
    from . import gameui
    info = gameui.dialog_info()
    if not info:
        return "no message box open"
    desc = f"{info['uuid']}: {' | '.join(info['texts']) or '(no text read)'} [{info['actions']} action(s)]"
    if not dismiss:
        return "open: " + desc
    closed, _ = gameui.dismiss_dialog()
    return ("dismissed: " if closed else "NOT dismissed (needs your answer or didn't close): ") + desc


@mcp.tool()
@guarded
def bg3_screenshot() -> str:
    """Capture the running game's window to a PNG (half size) and return its file path - open it with an image-capable
    Read to see what is on screen (dialogs, the HUD, a level-up screen). Needs the game running; it brings the game to the front first (a capture only sees what is visible), so use it while nobody is typing."""
    from . import gameui
    return gameui.screenshot()


@mcp.tool()
@guarded
def bg3_press_key(scan: int = 0x2E, hold_ms: int = 120, focus: bool = True) -> str:
    """An OS-level key press (SendInput, hardware scan code; default 0x2E = C) with the game brought to the foreground:
    what gameplay hotkeys read. Types into whatever has focus, so use it while nobody is typing. Scan codes: Esc 0x01,
    C 0x2E, I 0x17, Enter 0x1C, Space 0x39, Tab 0x0F. (Ext.Input and PostMessage only reach the UI layer.)"""
    from . import gameui
    return "sent" if gameui.send_key(scan, hold_ms, focus) else "failed (is the game running?)"


@mcp.tool()
@guarded
def bg3_click(x: int = 0, y: int = 0, right: bool = False, count: int = 1, shot: bool = True,
              points: list[list[int]] | None = None, screenshot: bool = False) -> str:
    """OS-level mouse click(s). By default (shot=True) coordinates are pixels of the LAST bg3_screenshot, so you can click
    what you see; they are sent as fractions of the window and hold at any resolution of the same aspect ratio
    (shot=False: game client pixels, 1920x1080 at full size). points=[[x, y], ...] clicks several in one call (e.g. a
    checklist row then the tiles you want); screenshot=True returns a fresh bg3_screenshot path afterwards, so a whole
    step is one call. Brings the game to the front, so use it while nobody is typing."""
    from . import gameui
    pts = points or [[x, y]]
    n = gameui.click_many(pts, shot=shot, right=right) if len(pts) > 1 else (1 if gameui.click(pts[0][0], pts[0][1], right, count, shot) else 0)
    if n == 0:
        return "failed (is the game running?)"
    msg = f"clicked {n}" + (f"/{len(pts)}" if n != len(pts) else "")
    if screenshot:
        msg += "; screenshot: " + gameui.screenshot()
    return msg


@mcp.tool()
@guarded
def bg3_levelup(action: str = "state", sheet_scan: int = 0x17, add_class: str | None = None, subclass: str | None = None) -> str:
    """Level-up screen helper (needs the character level-up ready: bg3_level_up grants the XP). action: 'state' (sheet /
    level-up screen open? is every choice made = IsLevelUpComplete), 'open' (character sheet key, then the LEVEL UP bar;
    sheet_scan is the scan code of the sheet key, 0x17 = I), 'finish' (accept via FinishLevelUp once complete, then waits until the new level is really applied), 'auto' (open, fill every pending choice - spells, cantrips, rituals, savant, a feat: Ability Improvement +2 to the primary ability, else the first free feat - accept, verify the level rose by one and run level_check; returns a log with timings). add_class (auto only): take the level in that class instead (multiclass; Barbarian, Bard, Cleric, Druid, Fighter, Monk, Paladin, Ranger, Rogue, Sorcerer, Warlock, Wizard) - the game pre-fills its first-level picks, and the class's level is verified; a class the character has is levelled from the class carousel. subclass (auto only): on a level that offers one, take this subclass (IDString, e.g. BattleMaster) instead of the game's default - verified on the character.
    The choices in between (class, subclass, spells, feat, ability points) are clicked with bg3_screenshot + bg3_click;
    after 'open' take a screenshot, click through the checklist on the left, and call 'state' until complete is true."""
    from . import gameui
    if action == "state":
        return json.dumps(gameui.levelup_state())
    if action == "open":
        return json.dumps(gameui.levelup_open(sheet_scan))
    if action == "finish":
        ok, msg = gameui.levelup_finish()
        return ("accepted " + msg).strip() if ok else "not accepted: " + msg
    if action == "auto":
        r = gameui.levelup_auto(add_class=add_class, subclass=subclass)
        if r.get("ok"):   # validate: the host must match its class progression at the new level
            from . import testing
            st, active = _testing_store(None)
            chk = testing.level_check(st, active)
            r["level_check"] = chk.splitlines()[0]
            r["level_check_fails"] = [l.strip() for l in chk.splitlines() if l.strip().startswith("FAIL")]
            if r["level_check_fails"]:
                r["ok"] = False
                r["error"] = f"level applied, but level_check found {len(r['level_check_fails'])} FAIL(s)"
        return json.dumps(r)
    return "action must be state, open, finish or auto"


@mcp.tool()
@guarded
def bg3_load_save(index: int = 0, name: str | None = None) -> str:
    """Load a save in the RUNNING game from the pause menu (no restart, ~15-40 s): Esc, Load Game, the save in row `index` of the
    list (0 = first row = the game's newest), Load Game; clears the [ForceUpdate] box and waits for a host. Clicks nothing unless
    the pause menu is confirmed open. Returns the host level, so a wrong row shows up immediately. name: load by (part of) the save's
    name instead - looked up in the game's list order (newest SaveTime first). Take a bg3_screenshot of
    the Load list first if unsure which row is which."""
    from . import gameui
    t, info = gameui.load_save(index, name=name)
    return f"loaded in {t}s, host level {info}" if t is not None else "not loaded: " + info


@mcp.tool()
@guarded
def bg3_deps_status(layer: str) -> str:
    """Dependency drift for a mod layer: each meta.lsx dependency that is also a layer (normally the deployed
    .pak), with its declared (meta.lsx), locked (bg3deps.lock.json) and current (indexed) release."""
    from . import deps
    return deps.status(store(), layer)


@mcp.tool()
@guarded
def bg3_deps_diff(layer: str, dep: str | None = None, limit: int = 60) -> str:
    """What changed in the mod's dependencies since its lock (stats entries, templates, progressions, lists,
    static data), with the changes the mod OVERRIDES (same entry/UUID) or REFERENCES (using, functors, lists...)
    listed first."""
    from . import deps
    return deps.diff(store(), layer, dep, _limit(limit, 60, 1000))


@mcp.tool()
@guarded
def bg3_deps_lock(layer: str) -> str:
    """Record the current release + a fingerprint snapshot of every tracked dependency as the mod's baseline
    (writes <mod>/bg3deps.lock.json). bg3_deps_update does this itself after a successful update."""
    from . import deps
    return deps.lock_deps(store(), layer)


@mcp.tool()
@guarded
def bg3_deps_update(layer: str, apply: bool = False) -> str:
    """Follow dependency drift: diff, run the mod's `regen` commands (layers.json), re-index, lint, bump the
    dependency Version64/MD5 in meta.lsx and rewrite the lock. apply=False (default) only shows the plan."""
    from . import deps
    return deps.update(store(), layer, apply=apply, log=_log)


@mcp.tool()
@guarded
def bg3_lint_progressions(layer: str, layers: list[str] | None = None) -> str:
    """Static progression checks for a mod layer: invalid node UUIDs (the game silently drops those
    nodes), selectors referencing lists no layer defines, and same table+level nodes from different
    layers (both load, so their choices/feats stack)."""
    from . import testing
    s = store()
    return testing.lint_progressions(s, s.active(layers), layer)


@mcp.tool()
@se_guarded
def bg3_test_plan(layer: str, build: str, layers: list[str] | None = None) -> str:
    """The step-by-step plan for a test build ([[build]] in the suite TOML): for every level the exact
    level-up choices (subclass, which spells, feat), chosen so every spell a test needs is learned by its
    test level, plus the tests that run there. Written to <mod>/docs/test-scripts/plan-<build>.md."""
    from . import testing
    s, active = _testing_store(layers)
    text, path = testing.plan(s, active, layer, build)
    return (f"written to {path}\n\n" if path else "") + text


@mcp.tool()
@se_guarded
def bg3_test_cleanup() -> str:
    """Remove every test spawn, boost (tag BG3Test) and applied status, and stop recording."""
    from . import testing
    r = testing.cleanup()
    return f"removed {r.get('spawns', 0)} spawns, {r.get('grants', 0)} boosts, {r.get('statuses', 0)} statuses"


@mcp.tool()
@se_guarded
def bg3_ingame_check(layer: str, layers: list[str] | None = None) -> str:
    """Compare EVERY stats entry a mod layer defines with what the running game loaded (the engine drops
    invalid values silently). `layers` = what's deployed (default: all)."""
    from . import groundtruth
    with _lock:
        s = store()
    lines = groundtruth.run(s, layers or [m["name"] for m in s.cfg["mods"]], only_layer=layer)
    return "\n".join(lines)


# ---------------------------------------------------------------------------- Larian Toolkit
# mod.io publishing goes through the Toolkit, which edits its own copy under Editor/Mods/<mod>/. These keep
# that copy generated from (and in step with) the game-ready files. Formats/mappings are learned from the
# vanilla editor data and mods shipping both copies (validated: 99.7% of dnd55e's 8107 objects reproduced).

@mcp.tool()
@guarded
def bg3_toolkit_status(layer: str) -> str:
    """Where the Larian Toolkit expects this mod (Data/Projects, Mods, Public, Editor/Mods): present or missing,
    linked to the repo or a separate copy, file counts, and the meta.lsx version (mod.io readiness)."""
    from . import toolkit
    return toolkit.status(layer)


@mcp.tool()
@guarded
def bg3_toolkit_check(layer: str, dest: str = "toolkit", limit: int = 80) -> str:
    """Compare the mod's Toolkit editor copy (dest='toolkit': <game>/Data/Editor/Mods/<mod>; 'repo':
    <mod>/Editor/Mods/<mod>) with an export of its game-ready files: missing/extra objects and differing
    fields per file. Run before publishing; 0 problems = both copies say the same."""
    from . import toolkit
    s = store()
    return toolkit.check(s, s.active(None), layer, dest, _limit(limit, 80, 1000))


@mcp.tool()
@guarded
def bg3_toolkit_export(layer: str, dest: str = "toolkit", write: bool = False) -> str:
    """Generate the Toolkit editor copy (.stats for stats, .tbl for lists/progressions/resources/classes/
    level maps/feats) from the mod's game-ready files. Existing editor UUIDs are kept. write=False is a dry
    run listing files, object counts and warnings; write=True writes them (dest 'toolkit' or 'repo').
    Close the Toolkit first: it overwrites files it has open."""
    from . import toolkit
    s = store()
    return toolkit.export(s, s.active(None), layer, dest, dry_run=not write)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
