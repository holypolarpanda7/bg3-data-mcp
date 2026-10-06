"""SQLite index of every layer. A layer is rebuilt only when its source signature changes."""
import json
import os
import re
import sqlite3
import time

from . import parse, sources

DB = os.path.join(sources.CACHE, "index.sqlite")
SCHEMA_VERSION = "4"  # 3: condition functions from .khn helpers (staticdata kind KhnFunction); 4: ProgressionDescription

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS layers(name TEXT PRIMARY KEY, kind TEXT, path TEXT, order_idx INT,
    signature TEXT, newest REAL, indexed_at REAL, counts TEXT);
CREATE TABLE IF NOT EXISTS stats(layer TEXT, rank INT, source TEXT, file TEXT, name TEXT, type TEXT,
    using_ TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS stats_name ON stats(name);
CREATE TABLE IF NOT EXISTS loca(layer TEXT, rank INT, source TEXT, handle TEXT, version INT, text TEXT);
CREATE INDEX IF NOT EXISTS loca_handle ON loca(handle);
CREATE TABLE IF NOT EXISTS templates(layer TEXT, rank INT, source TEXT, mapkey TEXT, name TEXT,
    parent TEXT, type TEXT, attrs TEXT);
CREATE INDEX IF NOT EXISTS tpl_key ON templates(mapkey);
CREATE INDEX IF NOT EXISTS tpl_name ON templates(name);
CREATE TABLE IF NOT EXISTS prog(layer TEXT, rank INT, source TEXT, uuid TEXT, table_uuid TEXT,
    name TEXT, level INT, attrs TEXT);
CREATE INDEX IF NOT EXISTS prog_table ON prog(table_uuid);
CREATE INDEX IF NOT EXISTS prog_name ON prog(name);
CREATE TABLE IF NOT EXISTS lists(layer TEXT, rank INT, source TEXT, node TEXT, uuid TEXT, name TEXT,
    attrs TEXT);
CREATE INDEX IF NOT EXISTS lists_uuid ON lists(uuid);
CREATE TABLE IF NOT EXISTS mei(layer TEXT, rank INT, source TEXT, uuid TEXT, name TEXT, effects TEXT);
CREATE INDEX IF NOT EXISTS mei_uuid ON mei(uuid);
CREATE TABLE IF NOT EXISTS fx(layer TEXT, rank INT, source TEXT, id TEXT, name TEXT, duration TEXT,
    looping TEXT, source_file TEXT);
CREATE TABLE IF NOT EXISTS staticdata(layer TEXT, rank INT, source TEXT, kind TEXT, uuid TEXT, name TEXT, attrs TEXT);
CREATE INDEX IF NOT EXISTS staticdata_key ON staticdata(kind, name, uuid);
CREATE INDEX IF NOT EXISTS fx_id ON fx(id);
"""
TABLES = ("stats", "loca", "templates", "prog", "lists", "mei", "fx", "staticdata")
# static-data nodes indexed generically (by UUID and Name): class/subclass descriptions, level maps
# (SuperiorityDie, proficiency...), action resources, feats, and the level-up screen's headings per selector id
STATIC_NODES = ("ClassDescription", "LevelMapSeries", "ActionResourceDefinition", "Feat", "ProgressionDescription")

LIST_NODES = ("SpellList", "PassiveList", "SkillList", "AbilityList", "EquipmentList")


def connect():
    os.makedirs(sources.CACHE, exist_ok=True)
    # one shared connection used from MCP worker threads; callers serialise access with a lock
    db = sqlite3.connect(DB, check_same_thread=False, isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    row = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
    if not row or row[0] != SCHEMA_VERSION:
        for t in ("layers",) + TABLES:
            db.execute(f"DELETE FROM {t}")
        db.execute("INSERT OR REPLACE INTO meta VALUES('schema', ?)", (SCHEMA_VERSION,))
    return db


def configured_layers(cfg):
    out = [{"name": "base", "kind": "base", "path": cfg["base"]["game_data"], "order_idx": 0}]
    for i, m in enumerate(cfg["mods"], start=1):
        out.append({"name": m["name"], "kind": "pak" if m["path"].lower().endswith(".pak") else "folder",
                    "path": m["path"], "order_idx": i, "mod": m})
    return out


def _clear(db, layer):
    for t in TABLES:
        db.execute(f"DELETE FROM {t} WHERE layer=?", (layer,))


def _ingest(db, layer, base_rank, files_by_kind):
    counts = {k: 0 for k in TABLES}
    rank = base_rank
    for source, path in files_by_kind.get("stats", []):
        rows = []
        for name, typ, using, data in parse.parse_stats(parse.read_text(path)):
            rank += 1
            rows.append((layer, rank, source, os.path.basename(path), name, typ, using, json.dumps(data)))
        db.executemany("INSERT INTO stats VALUES(?,?,?,?,?,?,?,?)", rows)
        counts["stats"] += len(rows)
    for source, path in files_by_kind.get("loca", []):
        rows = []
        for handle, version, text in parse.parse_loca(path):
            rank += 1
            rows.append((layer, rank, source, handle, version, text))
        db.executemany("INSERT INTO loca VALUES(?,?,?,?,?,?)", rows)
        counts["loca"] += len(rows)
    for source, path in files_by_kind.get("templates", []):
        rows = []
        for a in parse.parse_templates(path):
            rank += 1
            rows.append((layer, rank, source, a.get("MapKey"), a.get("Name"), a.get("ParentTemplateId"),
                         a.get("Type"), json.dumps(a)))
        db.executemany("INSERT INTO templates VALUES(?,?,?,?,?,?,?,?)", rows)
        counts["templates"] += len(rows)
    for source, path in files_by_kind.get("progressions", []):
        rows = []
        for a in parse.parse_nodes(path, "Progression"):
            rank += 1
            try:
                level = int(a.get("Level") or 0)
            except ValueError:
                level = 0
            rows.append((layer, rank, source, a.get("UUID"), a.get("TableUUID"), a.get("Name"), level, json.dumps(a)))
        db.executemany("INSERT INTO prog VALUES(?,?,?,?,?,?,?,?)", rows)
        counts["prog"] += len(rows)
    for source, path in files_by_kind.get("lists", []):
        ids = parse.list_node_ids(path)
        rows = []
        for node in LIST_NODES:
            if node not in ids:
                continue
            for a in parse.parse_nodes(path, node):
                rank += 1
                rows.append((layer, rank, source, node, a.get("UUID"), a.get("Name") or a.get("SelectorId"), json.dumps(a)))
        db.executemany("INSERT INTO lists VALUES(?,?,?,?,?,?,?)", rows)
        counts["lists"] += len(rows)
    rows = []
    for source, path in files_by_kind.get("mei", []):
        m = parse.parse_multieffect(path)
        if m and m[0]:
            rank += 1
            rows.append((layer, rank, source, m[0], m[1], json.dumps(m[2])))
    db.executemany("INSERT INTO mei VALUES(?,?,?,?,?,?)", rows)
    counts["mei"] += len(rows)
    for source, path in files_by_kind.get("fxbanks", []):
        rows = []
        for fid, name, dur, loop, src in parse.parse_effect_bank(path):
            rank += 1
            rows.append((layer, rank, source, fid, name, dur, loop, src))
        db.executemany("INSERT INTO fx VALUES(?,?,?,?,?,?,?,?)", rows)
        counts["fx"] += len(rows)
    for source, path in files_by_kind.get("staticdata", []):
        ids = parse.list_node_ids(path)
        rows = []
        for node in STATIC_NODES:
            if node not in ids:
                continue
            for a in parse.parse_nodes(path, node):
                rank += 1
                rows.append((layer, rank, source, node, a.get("UUID"), a.get("Name") or a.get("SelectorId"), json.dumps(a)))
        db.executemany("INSERT INTO staticdata VALUES(?,?,?,?,?,?,?)", rows)
        counts["staticdata"] += len(rows)
    # condition/functor helper functions (Scripts/thoth/helpers/*.khn): the names stats expressions can call
    rows = []
    for source, path in files_by_kind.get("khn", []):
        text = open(path, encoding="utf-8", errors="replace").read()
        for fn, args in re.findall(r"^\s*function\s+([A-Za-z_]\w*)\s*\(([^)]*)\)", text, re.M):
            rank += 1
            rows.append((layer, rank, source, "KhnFunction", None, fn, json.dumps({"args": args, "file": os.path.basename(path)})))
    db.executemany("INSERT INTO staticdata VALUES(?,?,?,?,?,?,?)", rows)
    counts["staticdata"] += len(rows)
    return counts


def build_layer(db, cfg, layer, log=print):
    t0 = time.time()
    name = layer["name"]
    base_rank = layer["order_idx"] * 10_000_000
    if layer["kind"] == "base":
        sig, newest = sources.base_signature(cfg)
        log(f"[base] extracting from game paks (newest pak {sources.iso(newest)})")
        sources.extract_base(cfg, log=log)
        files = {k: sources.base_files(cfg, k) for k in ("stats", "templates", "progressions", "lists", "mei", "fxbanks", "staticdata", "khn")}
        files["loca"] = [("base/Localization", f) for f in sources.base_loca_files()]
    else:
        mod = layer["mod"]
        sig, newest = sources.mod_signature(cfg, mod)
        files = {k: [(name, f) for f in sources.mod_files(cfg, mod, k)]
                 for k in ("stats", "templates", "progressions", "lists", "loca", "mei", "fxbanks", "staticdata", "khn")}
    # atomic: a failure mid-ingest rolls back and leaves the previous index for this layer intact
    db.execute("BEGIN")
    try:
        _clear(db, name)
        counts = _ingest(db, name, base_rank, files)
        db.execute("INSERT OR REPLACE INTO layers VALUES(?,?,?,?,?,?,?,?)",
                   (name, layer["kind"], layer["path"], layer["order_idx"], sig, newest, time.time(), json.dumps(counts)))
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    log(f"[{name}] indexed {counts} in {time.time() - t0:.1f}s")
    return counts


def refresh(db=None, cfg=None, force=None, log=print):
    """Rebuild layers whose sources changed (or those named in `force`). Returns list of rebuilt layers."""
    cfg = cfg or sources.load_config()
    db = db or connect()
    wanted = configured_layers(cfg)
    names = {l["name"] for l in wanted}
    for (old,) in db.execute("SELECT name FROM layers").fetchall():
        if old not in names:
            db.execute("BEGIN")
            _clear(db, old)
            db.execute("DELETE FROM layers WHERE name=?", (old,))
            db.execute("COMMIT")
    rebuilt, errors = [], {}
    for layer in wanted:
        row = db.execute("SELECT signature, order_idx FROM layers WHERE name=?", (layer["name"],)).fetchone()
        try:
            if layer["kind"] == "base":
                sig, _ = sources.base_signature(cfg)
            else:
                sig, _ = sources.mod_signature(cfg, layer["mod"])
        except Exception as e:
            errors[layer["name"]] = f"{type(e).__name__}: {e}"
            log(f"[{layer['name']}] cannot read sources, keeping previous index: {errors[layer['name']]}")
            continue
        if force and (force == "all" or layer["name"] in force) or not row or row[0] != sig or row[1] != layer["order_idx"]:
            try:
                build_layer(db, cfg, layer, log=log)
                rebuilt.append(layer["name"])
            except Exception as e:  # keep serving the old index for this layer (if there is one)
                had = row is not None and db.execute("SELECT count(*) FROM stats WHERE layer=?", (layer["name"],)).fetchone()[0] > 0
                errors[layer["name"]] = f"{type(e).__name__}: {e}" + ("" if had else " [NO previous index: layer is EMPTY]")
                log(f"[{layer['name']}] rebuild FAILED: {errors[layer['name']]}")
    refresh.last_errors = errors
    return rebuilt


refresh.last_errors = {}
