"""SQLite index of every layer. A layer is rebuilt only when its source signature changes."""
import json
import os
import sqlite3
import time

from . import parse, sources

DB = os.path.join(sources.CACHE, "index.sqlite")
SCHEMA_VERSION = "1"

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
"""

LIST_NODES = ("SpellList", "PassiveList", "SkillList", "AbilityList", "EquipmentList")


def connect():
    os.makedirs(sources.CACHE, exist_ok=True)
    db = sqlite3.connect(DB)
    db.executescript(SCHEMA)
    row = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
    if not row or row[0] != SCHEMA_VERSION:
        for t in ("layers", "stats", "loca", "templates", "prog", "lists"):
            db.execute(f"DELETE FROM {t}")
        db.execute("INSERT OR REPLACE INTO meta VALUES('schema', ?)", (SCHEMA_VERSION,))
        db.commit()
    return db


def configured_layers(cfg):
    out = [{"name": "base", "kind": "base", "path": cfg["base"]["game_data"], "order_idx": 0}]
    for i, m in enumerate(cfg["mods"], start=1):
        out.append({"name": m["name"], "kind": "pak" if m["path"].lower().endswith(".pak") else "folder",
                    "path": m["path"], "order_idx": i, "mod": m})
    return out


def _clear(db, layer):
    for t in ("stats", "loca", "templates", "prog", "lists"):
        db.execute(f"DELETE FROM {t} WHERE layer=?", (layer,))


def _ingest(db, layer, base_rank, files_by_kind):
    counts = {k: 0 for k in ("stats", "loca", "templates", "prog", "lists")}
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
                rows.append((layer, rank, source, node, a.get("UUID"), a.get("Name"), json.dumps(a)))
        db.executemany("INSERT INTO lists VALUES(?,?,?,?,?,?,?)", rows)
        counts["lists"] += len(rows)
    return counts


def build_layer(db, cfg, layer, log=print):
    t0 = time.time()
    name = layer["name"]
    base_rank = layer["order_idx"] * 10_000_000
    if layer["kind"] == "base":
        sig, newest = sources.base_signature(cfg)
        log(f"[base] extracting from game paks (newest pak {sources.iso(newest)})")
        sources.extract_base(cfg, log=log)
        files = {k: sources.base_files(cfg, k) for k in ("stats", "templates", "progressions", "lists")}
        files["loca"] = [("base/Localization", f) for f in sources.base_loca_files()]
    else:
        mod = layer["mod"]
        sig, newest = sources.mod_signature(cfg, mod)
        files = {k: [(name, f) for f in sources.mod_files(cfg, mod, k)]
                 for k in ("stats", "templates", "progressions", "lists", "loca")}
    _clear(db, name)
    counts = _ingest(db, name, base_rank, files)
    db.execute("INSERT OR REPLACE INTO layers VALUES(?,?,?,?,?,?,?,?)",
               (name, layer["kind"], layer["path"], layer["order_idx"], sig, newest, time.time(), json.dumps(counts)))
    db.commit()
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
            _clear(db, old)
            db.execute("DELETE FROM layers WHERE name=?", (old,))
    rebuilt = []
    for layer in wanted:
        row = db.execute("SELECT signature, order_idx FROM layers WHERE name=?", (layer["name"],)).fetchone()
        if layer["kind"] == "base":
            sig, _ = sources.base_signature(cfg)
        else:
            sig, _ = sources.mod_signature(cfg, layer["mod"])
        if force and (force == "all" or layer["name"] in force) or not row or row[0] != sig or row[1] != layer["order_idx"]:
            build_layer(db, cfg, layer, log=log)
            rebuilt.append(layer["name"])
    db.commit()
    return rebuilt
