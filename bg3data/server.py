"""MCP server: layered BG3 game-data lookup (base game + additive mod layers)."""
import json
import sys
import threading
import time

from mcp.server.mcpserver import MCPServer

from . import format as fmt
from . import index, query, sources

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
_lock = threading.Lock()
_last_check = 0.0


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
            index.refresh(_store.db, sources.load_config(), log=_log)
            _store.cfg = sources.load_config()
            _last_check = time.time()
        return _store


@mcp.tool()
def bg3_layers() -> str:
    """List data layers in load order with source timestamps and entry counts."""
    return fmt.layers(store())


@mcp.tool()
def bg3_refresh(force: str | None = None) -> str:
    """Re-index layers whose sources changed. force: a layer name, or 'all', to rebuild regardless."""
    s = store()
    rebuilt = index.refresh(s.db, sources.load_config(), force=[force] if force and force != "all" else force, log=_log)
    return f"rebuilt: {rebuilt or 'nothing (all up to date)'}\n\n" + fmt.layers(s)


@mcp.tool()
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
    sources.save_config(cfg)
    s = store()
    index.refresh(s.db, cfg, log=_log)
    s.cfg = cfg
    return fmt.layers(s)


@mcp.tool()
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
    return fmt.layers(s)


@mcp.tool()
def bg3_get_entry(name: str, layers: list[str] | None = None, provenance: bool = True) -> str:
    """Resolve a stats entry (spell, status, passive, character, object, weapon, ...) through inheritance
    across the selected layers. Shows the `using` chain and, per field, the layer/source that set it."""
    s = store()
    return fmt.entry(s, name, s.active(layers), provenance)


@mcp.tool()
def bg3_search(text: str, type: str | None = None, field: str | None = None,
               layers: list[str] | None = None, limit: int = 50) -> str:
    """Find stats entries whose name or any field contains `text`. type: SpellData, StatusData,
    PassiveData, Character, Object, Weapon, Armor, InterruptData... field: restrict to one field."""
    s = store()
    rows = s.search_stats(text, s.active(layers), type, field, limit)
    return "\n".join(f"{n} ({t}) [{l}]" for n, t, l in rows) or "no matches"


@mcp.tool()
def bg3_references(token: str, layers: list[str] | None = None, limit: int = 100) -> str:
    """Everything that mentions `token` (entry name, status, passive, GUID...) in stats, templates,
    progressions and lists."""
    s = store()
    rows = s.references(token, s.active(layers), limit)
    return "\n".join(f"{t:9s} {n} [{src}]" for t, n, l, src in rows) or "no references"


@mcp.tool()
def bg3_diff(name: str, layer: str, layers: list[str] | None = None) -> str:
    """What `layer` changes about stats entry NAME compared with the layers below it."""
    s = store()
    active = s.active(layers)
    if layer not in active:
        return f"layer '{layer}' is not in the active stack {active}"
    return fmt.diff(s, name, layer, active)


@mcp.tool()
def bg3_loca(query: str, layers: list[str] | None = None, limit: int = 30) -> str:
    """Look up a localization handle (e.g. h1a950eaag...), or search localized text."""
    s = store()
    active = s.active(layers)
    q = query.strip()
    if " " not in q and q.startswith("h") and len(q.split(";")[0]) >= 20:
        r = s.loca(q, active)
        return f"{r[0]}    [{r[1]}, v{r[2]}]" if r else "handle not found"
    rows = s.loca_search(q, active, limit)
    return "\n".join(f"{h}  {t[:160]}  [{src}]" for h, t, src in rows) or "no matches"


@mcp.tool()
def bg3_template(key: str, layers: list[str] | None = None) -> str:
    """Resolve a root template (MapKey GUID or Name) through its ParentTemplateId chain."""
    s = store()
    return fmt.template(s, key, s.active(layers))


@mcp.tool()
def bg3_progression(key: str, level: int | None = None, layers: list[str] | None = None) -> str:
    """Progression nodes for a class/subclass (Name, e.g. 'Soulknife', or TableUUID), merged by node UUID
    so a higher layer's node replaces a lower one's."""
    s = store()
    return fmt.progression(s, key, s.active(layers), level)


@mcp.tool()
def bg3_spell_list(key: str, layers: list[str] | None = None) -> str:
    """A spell/passive/skill list by UUID or Name (highest layer wins)."""
    s = store()
    r = s.spell_list(key, s.active(layers))
    if not r:
        return "list not found"
    node, uuid, name, src, attrs = r
    a = json.loads(attrs)
    return f"{node} {name} {uuid} [{src}]\n" + "\n".join(f"  {k} = {v}" for k, v in a.items() if k not in ("UUID", "Name"))


@mcp.tool()
def bg3_spell_visuals(name: str, layers: list[str] | None = None) -> str:
    """The visual/audio kit of a spell: effects, animations, sounds, icon - for reusing vanilla VFX."""
    s = store()
    active = s.active(layers)
    r = s.resolve(name, active)
    if not r:
        return f"'{name}' not found"
    kit = {k: (v, src) for k, (v, src) in r["fields"].items() if query.VISUAL_KEYS.search(k) and v}
    return f"{name} - \"{s.display_name(r['fields'], active)}\"\n" + "\n".join(f"  {k} = {v}    [{src}]" for k, (v, src) in sorted(kit.items()))


@mcp.tool()
def bg3_similar_spells(damage_type: str | None = None, school: str | None = None, spell_type: str | None = None,
                       level: int | None = None, keyword: str | None = None,
                       layers: list[str] | None = None, limit: int = 25) -> str:
    """Spells matching damage type / school / spell type (Target, Projectile, Shout, Zone...) / level /
    name keyword, with their visual kits - to borrow effects and animations for new spells."""
    s = store()
    rows = s.similar_spells(s.active(layers), damage_type, school, spell_type, level, keyword, limit)
    out = []
    for n, dn, lvl, sch, kit in rows:
        out.append(f"{n} - \"{dn}\" L{lvl} {sch}")
        for k in sorted(kit):
            if "Effect" in k or "Animation" in k or "Sound" in k:
                out.append(f"    {k} = {kit[k][:120]}")
    return "\n".join(out) or "no matches"


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
