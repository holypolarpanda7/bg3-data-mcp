"""Visual effects (2026-10-12): fingerprints, provenance, diff, lint and recolour builds for .lsfx effects.

An effect resource (.lsfx) is an LSF resource: a timeline of components (ParticleSystem, Light, Model, OverlayMaterial...),
each a list of properties. LSLib converts it to .lsx and back losslessly (Divine only takes the .lsf extension, so copies
are renamed). Every colour is a property of Type 3 whose FullName ends in "Color", holding keyframes
`<attribute id="Color" type="fvec4" value="r g b a">` (r/g/b can exceed 1 for HDR). A Light only uses its own colour when
"Appearance.Override Light Template Color" is True; otherwise its Light UUID template's colour shows.

Component IDs (GUIDs) survive copying an effect, and Larian reuses them across its own effects. That makes provenance work:
each component of a mod effect is matched to the vanilla effect that shares the most component IDs, then its colour and
non-colour values are compared (floats rounded to 3 places: re-saved files differ in the last digits). Found by tracing Vlad's
Grimoire back to vanilla (2026-10-12): about half of its 304 effects were vanilla files with only colours changed.

Vanilla fingerprints live in cache/fx/fingerprints.sqlite (built once per game patch by `build_index`: every .lsfx from
Effects.pak and the base paks, later paks win). Per component only hashes are kept; the property-level detail comes from
re-reading the one file involved (extracted and converted on demand into cache/fx/lsx/).

Recolour builds read <mod>/Scripts/data/vfx.toml and write, from vanilla sources only, new .lsfx files, one Content bank and
new MultiEffectInfos into the mod, with stable UUIDs (uuid5 of layer + names), plus Scripts/data/vfx_generated.json (name ->
MultiEffectInfo UUID for stats *Effect fields, and the files written so the next build can remove stale ones).
"""
import colorsys
import glob
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import tomllib
import uuid
from concurrent.futures import ProcessPoolExecutor

from . import sources

FX = os.path.join(sources.CACHE, "fx")
DB = os.path.join(FX, "fingerprints.sqlite")
NS = uuid.UUID("6a8f3c2e-5b1d-4e7a-9c40-2f1b8d7e6a51")   # uuid5 namespace for generated effect UUIDs; never change

ATTR = re.compile(r'<attribute id="([^"]+)" type="([^"]+)" value="([^"]*)"')
NODE = re.compile(r'<node id="([^"]+)"')
GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
LIGHT_OVERRIDE = "Appearance.Override Light Template Color"
# colour properties a recolour leaves alone by default: screen-space darkening / grading isn't the effect's own colour
KEEP_DEFAULT = ("Darken Environment.Color",)
FLOATS = ("float", "fvec2", "fvec3", "fvec4", "double")

PRESETS = {   # name -> target colour; mode "hue" keeps white/grey keyframes (texture colour shows), "tint" colours them too
    "blood": {"color": "#8a0610", "mode": "tint", "brightness": 0.75},
    "crimson": {"color": "#c0102a", "mode": "hue"},
    "necrotic": {"color": "#3fae4a", "mode": "hue"},
    "radiant": {"color": "#ffcf4a", "mode": "hue"},
    "arcane": {"color": "#8a4dff", "mode": "hue"},
    "psychic": {"color": "#ff4fd8", "mode": "hue"},
    "frost": {"color": "#7fd8ff", "mode": "hue"},
    "fire": {"color": "#ff6a1a", "mode": "hue"},
    "acid": {"color": "#a8e02a", "mode": "hue"},
    "shadow": {"color": "#2a1840", "mode": "tint"},
}


# ------------------------------------------------------------------------------------------------ parsing

def _norm(v, t):
    if t in FLOATS:
        try:
            return " ".join(f"{float(x):.3f}" for x in v.split())
        except ValueError:
            return v
    return v


def _is_colour_prop(name):
    return bool(name) and (name == "Color" or name.endswith(".Color"))


def parse(path):
    """An effect .lsx -> {dur, deps, comps: [{id, type, name, start, end, props: {FullName: (colours, others)}}]}."""
    comps, deps, cur, prop, dur, in_effect = [], [], None, None, None, False
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            n = NODE.search(line)
            if n:
                nid = n.group(1)
                if nid == "EffectComponent":
                    cur = {"id": None, "type": None, "name": None, "start": None, "end": None, "props": {}}
                    comps.append(cur)
                    prop = None
                elif nid == "Property" and cur is not None:
                    prop = {"name": None, "color": [], "other": []}
                elif nid == "Effect":
                    in_effect = True
                continue
            a = ATTR.search(line)
            if not a:
                continue
            k, t, v = a.groups()
            if k == "Object":
                deps.append(v)
            elif in_effect and cur is None and k == "Duration":
                dur = _norm(v, t)
            elif cur is not None and prop is None:
                key = {"ID": "id", "Type": "type", "Name": "name", "StartTime": "start", "EndTime": "end"}.get(k)
                if key:
                    cur[key] = _norm(v, t)
            elif prop is not None:
                if k == "FullName":
                    prop["name"] = v
                    cur["props"][v] = prop
                elif k in ("AttributeName", "Type"):
                    pass
                elif k == "Color" and t == "fvec4":
                    prop["color"].append(_norm(v, t))
                else:
                    prop["other"].append(f"{k}={_norm(v, t)}")
    for c in comps:
        c["props"] = {k: (tuple(p["color"]), tuple(p["other"])) for k, p in c["props"].items()}
    return {"dur": dur, "deps": sorted(set(deps)), "comps": comps}


def _h(x):
    return hashlib.blake2b(repr(x).encode(), digest_size=6).hexdigest()


def comp_hashes(c):
    """(colour hash, other hash) of a parsed component; timing counts as 'other'."""
    props = c["props"]
    return (_h(sorted((k, v[0]) for k, v in props.items() if v[0])),
            _h((c["start"], c["end"], sorted((k, v[1]) for k, v in props.items()))))


def _rgb(s):
    return [float(x) for x in s.split()]


def _hsv(col):
    r, g, b, a = (_rgb(col) + [1, 1, 1, 1])[:4]
    m = max(r, g, b, 1e-6)
    h, s, _ = colorsys.rgb_to_hsv(r / m, g / m, b / m)
    return h, s, m, a


def palette(eff):
    """Dominant colour of a parsed effect: {bucket, hue, sat, white, frames}. A ParticleSystem's System.Appearance.Color
    tints its particles, so white particle keys under a coloured system colour count as that colour. 'white' = share of
    keys still white/grey (their colour comes from the texture, so a hue shift leaves them as they are)."""
    hx = hy = wsum = sat = 0.0
    white = n = coloured = 0
    for c in eff["comps"]:
        sys_keys = [_hsv(x) for x in c["props"].get("System.Appearance.Color", ((), ()))[0]]
        sys_col = [k for k in sys_keys if k[1] >= 0.12 and k[3] >= 0.02]
        tint = None
        if sys_col:
            tx = sum(math.cos(k[0] * 2 * math.pi) * k[1] for k in sys_col)
            ty = sum(math.sin(k[0] * 2 * math.pi) * k[1] for k in sys_col)
            tint = ((math.atan2(ty, tx) / (2 * math.pi)) % 1.0, sum(k[1] for k in sys_col) / len(sys_col))
        for name, (cols, _) in c["props"].items():
            if name in KEEP_DEFAULT or name.startswith("Color Grading") or name == "System.Appearance.Color":
                continue
            for col in cols:
                h, s, m, a = _hsv(col)
                if a < 0.02:
                    continue
                n += 1
                if s < 0.12:
                    if not tint:
                        white += 1
                        continue
                    h, s = tint
                w = s * min(m, 1.0)
                hx += math.cos(h * 2 * math.pi) * w
                hy += math.sin(h * 2 * math.pi) * w
                wsum += w
                sat += s
                coloured += 1
    if not n:
        return {"bucket": "none", "hue": None, "sat": 0, "white": 0, "frames": 0}
    hue = (math.degrees(math.atan2(hy, hx)) % 360) if wsum else None
    bucket = _bucket(hue) if hue is not None and coloured >= max(1, 0.15 * n) else "white"
    return {"bucket": bucket, "hue": None if hue is None else round(hue), "sat": round(sat / coloured, 2) if coloured else 0,
            "white": round(white / n, 2), "frames": n}


BUCKETS = [(15, "red"), (40, "orange"), (70, "yellow"), (160, "green"), (200, "cyan"), (250, "blue"), (290, "purple"),
           (345, "magenta"), (361, "red")]


def _bucket(h):
    return next(name for top, name in BUCKETS if h < top)


# ------------------------------------------------------------------------------------------------ vanilla files

def _paks(cfg):
    data = cfg["base"]["game_data"]
    out = [os.path.join(data, "Effects.pak")] + sources.base_paks(cfg)
    return [p for i, p in enumerate(out) if os.path.exists(p) and p not in out[:i]]


def _signature(cfg):
    return json.dumps([(os.path.basename(p), int(os.path.getmtime(p)), os.path.getsize(p)) for p in _paks(cfg)])


def _listing(cfg):
    """{source path: pak} for every vanilla .lsfx (later paks win), cached per pak signature."""
    path = os.path.join(FX, "listing.json")
    sig = _signature(cfg)
    try:
        d = json.load(open(path))
        if d["sig"] == sig:
            return d["files"]
    except (OSError, ValueError, KeyError):
        pass
    files = {}
    for pak in _paks(cfg):
        for line in sources.divine(cfg, "-a", "list-package", "-s", sources.winpath(pak)).splitlines():
            name = line.split("\t")[0]
            if name.lower().endswith(".lsfx"):
                files[name] = pak
    os.makedirs(FX, exist_ok=True)
    json.dump({"sig": sig, "files": files}, open(path, "w"))
    return files


def _convert(cfg, src, lsx):
    """One .lsfx (any path) -> .lsx, cached by mtime."""
    if os.path.exists(lsx) and os.path.getmtime(lsx) >= os.path.getmtime(src):
        return lsx
    os.makedirs(os.path.dirname(lsx), exist_ok=True)
    tmp = lsx[:-4] + ".tmp.lsf"
    shutil.copyfile(src, tmp)
    try:
        sources.divine(cfg, "-a", "convert-resource", "-s", sources.winpath(tmp), "-d", sources.winpath(lsx))
    finally:
        os.remove(tmp)
    return lsx


def effect_lsx(cfg, layer, source_file):
    """The .lsx of an effect resource's .lsfx: base files are extracted from their pak, mod files read from the layer."""
    if layer == "base":
        lsx = os.path.join(FX, "lsx", "base", source_file + ".lsx")
        if os.path.exists(lsx):
            return lsx
        listing = _listing(cfg)
        pak = listing.get(source_file) or next((p for f, p in listing.items() if f.lower() == source_file.lower()), None)
        if not pak:
            raise FileNotFoundError(f"{source_file} is in no game pak")
        raw = os.path.join(FX, "raw", "base", source_file)
        os.makedirs(os.path.dirname(raw), exist_ok=True)
        sources.divine(cfg, "-a", "extract-single-file", "-s", sources.winpath(pak), "-d", sources.winpath(raw), "-f", source_file)
        try:
            return _convert(cfg, raw, lsx)
        finally:
            os.remove(raw)
    mod = next((m for m in cfg["mods"] if m["name"] == layer), None)
    if not mod:
        raise ValueError(f"unknown layer {layer!r}")
    src = os.path.join(sources.mod_root(cfg, mod), source_file)
    if not os.path.exists(src):
        raise FileNotFoundError(f"{layer}: its effect bank points at {source_file}, which isn't in the mod")
    return _convert(cfg, src, os.path.join(FX, "lsx", layer, source_file + ".lsx"))


def mod_effects(cfg, layer):
    """{source path: .lsx} for every .lsfx a mod layer ships (converted in one Divine batch when stale)."""
    mod = next((m for m in cfg["mods"] if m["name"] == layer), None)
    if not mod:
        raise ValueError(f"unknown layer {layer!r}")
    root = sources.mod_root(cfg, mod)
    out, stale = {}, []
    for f in glob.glob(os.path.join(root, "Public", "**", "*.lsfx"), recursive=True):
        rel = os.path.relpath(f, root).replace("\\", "/")
        lsx = os.path.join(FX, "lsx", layer, rel + ".lsx")
        out[rel] = lsx
        if not (os.path.exists(lsx) and os.path.getmtime(lsx) >= os.path.getmtime(f)):
            stale.append((f, rel))
    if stale:
        stage = os.path.join(FX, "stage", layer)
        shutil.rmtree(stage, ignore_errors=True)
        for f, rel in stale:
            dst = os.path.join(stage, rel[:-5] + ".lsf")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(f, dst)
        conv = os.path.join(FX, "stage", layer + "_lsx")
        shutil.rmtree(conv, ignore_errors=True)
        sources.divine(cfg, "-a", "convert-resources", "-s", sources.winpath(stage), "-d", sources.winpath(conv), "-i", "lsf", "-o", "lsx")
        for f, rel in stale:
            lsx = out[rel]
            os.makedirs(os.path.dirname(lsx), exist_ok=True)
            shutil.move(os.path.join(conv, rel[:-5] + ".lsx"), lsx)
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(conv, ignore_errors=True)
    return out


# ------------------------------------------------------------------------------------------------ fingerprint index

def _db():
    os.makedirs(FX, exist_ok=True)
    db = sqlite3.connect(DB)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, pak TEXT, dur TEXT, ncomp INT, deps TEXT, bucket TEXT,
                                         hue INT, sat REAL, white REAL);
        CREATE TABLE IF NOT EXISTS comps(cid TEXT, path TEXT, idx INT, type TEXT, name TEXT, ch TEXT, oh TEXT);
        CREATE INDEX IF NOT EXISTS comps_cid ON comps(cid);
        CREATE INDEX IF NOT EXISTS comps_path ON comps(path);
    """)
    return db


def _fingerprint(args):
    rel, lsx = args
    e = parse(lsx)
    return rel, e["dur"], e["deps"], palette(e), [(c["id"], i, c["type"], c["name"], *comp_hashes(c)) for i, c in enumerate(e["comps"])]


def index_status(cfg=None):
    cfg = cfg or sources.load_config()
    if not os.path.exists(DB):
        return None
    db = _db()
    row = db.execute("SELECT value FROM meta WHERE key='sig'").fetchone()
    n = db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    return {"current": bool(row) and row[0] == _signature(cfg), "files": n}


def build_index(cfg=None, force=False, log=print):
    """Fingerprint every vanilla .lsfx (about 6 minutes; once per game patch). Temporary files are removed afterwards."""
    cfg = cfg or sources.load_config()
    st = index_status(cfg)
    if st and st["current"] and not force:
        return f"vanilla effect fingerprints are current ({st['files']} effects)"
    listing = _listing(cfg)
    tmp = os.path.join(FX, "build.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    by_pak = {}
    for f, pak in listing.items():
        by_pak.setdefault(pak, []).append(f)
    jobs = []
    for pak, files in by_pak.items():
        dest = os.path.join(tmp, os.path.splitext(os.path.basename(pak))[0])
        log(f"  {os.path.basename(pak)}: extracting {len(files)} effects")
        sources.divine(cfg, "-a", "extract-package", "-s", sources.winpath(pak), "-d", sources.winpath(dest), "-x", "*.lsfx")
        for f in files:
            p = os.path.join(dest, f)
            if os.path.exists(p):
                os.rename(p, p[:-5] + ".lsf")
                jobs.append((f, p[:-5] + ".lsx"))
        log(f"  {os.path.basename(pak)}: converting")
        sources.convert_lsf_tree(cfg, dest)
    log(f"  fingerprinting {len(jobs)} effects")
    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1)) as ex:
        rows = list(ex.map(_fingerprint, [j for j in jobs if os.path.exists(j[1])], chunksize=40))
    db = _db()
    db.execute("DELETE FROM files")
    db.execute("DELETE FROM comps")
    for rel, dur, deps, pal, comps in rows:
        db.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?)",
                   (rel, os.path.basename(listing[rel]), dur, len(comps), json.dumps(deps), pal["bucket"], pal["hue"], pal["sat"], pal["white"]))
        db.executemany("INSERT INTO comps VALUES(?,?,?,?,?,?,?)", [(c[0], rel, c[1], c[2], c[3], c[4], c[5]) for c in comps])
    db.execute("INSERT OR REPLACE INTO meta VALUES('sig', ?)", (_signature(cfg),))
    db.commit()
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(os.path.join(FX, "lsx", "base"), ignore_errors=True)   # on-demand copies may be from the previous patch
    return f"fingerprinted {len(rows)} vanilla effects ({sum(len(r[4]) for r in rows)} components)"


def _need_index(cfg=None):
    st = index_status(cfg or sources.load_config())
    if not st:
        raise RuntimeError("no vanilla effect fingerprints yet: run bg3_fx_index (about 6 minutes)")
    return None if st["current"] else "note: the game changed since the effect fingerprints were built; run bg3_fx_index"


# ------------------------------------------------------------------------------------------------ resolving

def resolve(store, ref, active):
    """Effect resources for a reference: an effect resource GUID or name, or a MultiEffectInfo GUID or name.
    -> (label, [ {layer, id, name, file} ])."""
    ref = (ref or "").strip()
    w, p = store._where(active)
    q = f"SELECT layer, id, name, source_file FROM fx WHERE {w} AND {{}} ORDER BY rank DESC LIMIT 1"
    key = ref.lower() if GUID.match(ref.lower()) else ref
    row = store.db.execute(q.format("id=?"), p + [key]).fetchone() or store.db.execute(q.format("name=?"), p + [ref]).fetchone()
    if row:
        return f"effect {row[2]}", [dict(zip(("layer", "id", "name", "file"), row))]
    m = store.db.execute(f"SELECT uuid, name, effects, layer FROM mei WHERE {w} AND (uuid=? OR name=?) ORDER BY rank DESC LIMIT 1",
                         p + [key, ref]).fetchone()
    if not m:
        raise ValueError(f"no effect resource or MultiEffectInfo {ref!r} in layers {active}")
    out = []
    for e in json.loads(m[2]):
        rid = e.get("resource")
        r = store.db.execute(q.format("id=?"), p + [rid]).fetchone() if rid else None
        if r:
            out.append(dict(zip(("layer", "id", "name", "file"), r)))
    return f"MultiEffectInfo {m[1]} ({m[0]}, {m[3]})", out


# ------------------------------------------------------------------------------------------------ diff

def _fmt_cols(cols, n=3):
    s = " | ".join(cols[:n])
    return s + (f" ... ({len(cols)} keys)" if len(cols) > n else "")


def diff_parsed(a, b, limit=40):
    """Component-level differences between two parsed effects; float noise is already rounded away."""
    lines = []
    bi = {c["id"]: c for c in b["comps"] if c["id"]}
    used = set()
    pairs = []
    for c in a["comps"]:
        o = bi.get(c["id"])
        if o is None:   # no shared ID: pair by type + name, in order
            o = next((x for x in b["comps"] if id(x) not in used and x["type"] == c["type"] and x["name"] == c["name"]), None)
        if o is not None:
            used.add(id(o))
        pairs.append((c, o))
    added = [x for x in b["comps"] if id(x) not in used]
    same = 0
    for c, o in pairs:
        label = f"{c['type']} '{c['name']}'"
        if o is None:
            lines.append(f"  - removed {label}")
            continue
        ch = []
        if (c["start"], c["end"]) != (o["start"], o["end"]):
            ch.append(f"timing {c['start']}-{c['end']} -> {o['start']}-{o['end']}")
        for k in sorted(set(c["props"]) | set(o["props"])):
            x, y = c["props"].get(k, ((), ())), o["props"].get(k, ((), ()))
            if x[0] != y[0]:
                ch.append(f"{k}: {_fmt_cols(x[0])}  ->  {_fmt_cols(y[0])}")
            if x[1] != y[1]:
                xs, ys = set(x[1]), set(y[1])
                ch.append(f"{k}: {', '.join(sorted(xs - ys))[:120] or '-'}  ->  {', '.join(sorted(ys - xs))[:120] or '-'}")
        if ch:
            lines.append(f"  ~ {label}" + ("" if c["id"] == o["id"] else " (matched by name)"))
            lines += [f"      {x}" for x in ch[:12]] + ([f"      ... {len(ch) - 12} more"] if len(ch) > 12 else [])
        else:
            same += 1
    lines += [f"  + added {x['type']} '{x['name']}'" for x in added]
    head = f"  {same} component(s) identical, {len(pairs) - same - sum(1 for _, o in pairs if o is None)} changed, " \
           f"{sum(1 for _, o in pairs if o is None)} removed, {len(added)} added"
    if a["dur"] != b["dur"]:
        head += f"; duration {a['dur']} -> {b['dur']}"
    return [head] + lines[:limit]


def diff(store, a, b, active, limit=60):
    cfg = sources.load_config()
    la, ra = resolve(store, a, active)
    lb, rb = resolve(store, b, active)
    if len(ra) != len(rb):
        note = [f"note: {len(ra)} vs {len(rb)} effect resources; pairing by position"]
    else:
        note = []
    out = [f"A: {la}", f"B: {lb}"] + note
    for x, y in zip(ra, rb):
        pa = parse(effect_lsx(cfg, x["layer"], x["file"]))
        pb = parse(effect_lsx(cfg, y["layer"], y["file"]))
        out.append(f"{x['name']} [{x['layer']}]  vs  {y['name']} [{y['layer']}]")
        out += diff_parsed(pa, pb, limit)
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------ provenance

def _classify(comps, db, ranked_files):
    """comps: [(cid, ch, oh)] of one effect -> (kind, sources {path: n}, new, colour_only, edited, per-comp detail)."""
    srcs, new, colour_only, edited, detail = {}, 0, 0, 0, []
    for cid, ch, oh in comps:
        rows = db.get(cid, {})
        if not rows:
            new += 1
            detail.append((cid, None, "new"))
            continue
        vf = next(f for f in ranked_files if f in rows)
        vch, voh = rows[vf]
        srcs[vf] = srcs.get(vf, 0) + 1
        if oh != voh:
            edited += 1
            detail.append((cid, vf, "edited"))
        elif ch != vch:
            colour_only += 1
            detail.append((cid, vf, "colour"))
        else:
            detail.append((cid, vf, "same"))
    n = len(comps)
    if n and not new and len(srcs) == 1:
        kind = ("identical copy" if not colour_only else "recolour") if not edited else "recolour + edits"
    elif n and new < n:
        kind = "combined" if len(srcs) > 1 else "vanilla base + new parts"
    else:
        kind = "new"
    return kind, srcs, new, colour_only, edited, detail


def _vanilla_lookup(vdb, cids):
    """{cid: {path: (ch, oh)}} from the fingerprint index."""
    out = {}
    cids = [c for c in set(cids) if c]
    for i in range(0, len(cids), 500):
        chunk = cids[i:i + 500]
        for cid, path, ch, oh in vdb.execute(f"SELECT cid, path, ch, oh FROM comps WHERE cid IN ({','.join('?' * len(chunk))})", chunk):
            out.setdefault(cid, {})[path] = (ch, oh)
    return out


def provenance(store, layer, effect=None, against=None, limit=60):
    """Where a mod layer's effects come from: each .lsfx traced to vanilla effects by component IDs."""
    cfg = sources.load_config()
    warn = _need_index(cfg)
    vdb = _db()
    files = mod_effects(cfg, layer)
    if effect:
        files = {k: v for k, v in files.items() if effect.lower() in k.lower()}
        if not files:
            return f"{layer}: no effect file matching {effect!r}"
    parsed = {}
    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1)) as ex:
        for rel, e in zip(files, ex.map(parse, files.values(), chunksize=8)):
            parsed[rel] = e
    # other mod layers to check copying against (e.g. a library we must not derive from)
    other = {}
    for o in against or []:
        for rel, lsx in mod_effects(cfg, o).items():
            for c in parse(lsx)["comps"]:
                other.setdefault((c["id"], *comp_hashes(c)), rel)
    vfiles = {r[0]: r for r in vdb.execute("SELECT path, pak, dur, ncomp, deps FROM files")}
    vdeps = set()
    for r in vfiles.values():
        vdeps.update(json.loads(r[4]))
    rows, flags = [], []
    for rel, e in sorted(parsed.items()):
        comps = [(c["id"], *comp_hashes(c)) for c in e["comps"]]
        look = _vanilla_lookup(vdb, [c[0] for c in comps])
        votes, exact = {}, {}
        for cid, _, oh in comps:
            for f, (_, voh) in look.get(cid, {}).items():
                votes[f] = votes.get(f, 0) + 1
                exact[f] = exact.get(f, 0) + (voh == oh)
        # Larian reuses component IDs across its own effects: most shared IDs first, then most components whose non-colour
        # values match exactly (the real source of a recolour), then the closest component count
        ranked = sorted(votes, key=lambda f: (-votes[f], -exact[f], abs(vfiles[f][3] - len(comps)) if f in vfiles else 0))
        kind, srcs, new, col, ed, detail = _classify(comps, look, ranked)
        copied = sorted({other[c] for c in comps if c in other and (not look.get(c[0]) or all(v != c[1:] for v in look[c[0]].values()))})
        own_deps = [d for d in e["deps"] if d not in vdeps]
        rows.append((rel, kind, len(comps), new, col, ed, srcs, own_deps, copied, e, detail))
        if copied:
            flags.append(f"  {rel.split('/')[-1]}: {len(copied)} component(s) identical to {', '.join(against)} ({copied[0].split('/')[-1]}) and not to vanilla")
    out = [f"Effect provenance for {layer}: {len(rows)} effect file(s) vs vanilla fingerprints" + (f"; checked against {', '.join(against)}" if against else "")]
    if warn:
        out.append(warn)
    kinds = {}
    for r in rows:
        kinds[r[1]] = kinds.get(r[1], 0) + 1
    out.append("  " + ", ".join(f"{k}: {n}" for k, n in sorted(kinds.items(), key=lambda x: -x[1])))
    nd = sum(1 for r in rows if r[7])
    if nd:
        out.append(f"  {nd} effect(s) use materials/textures/models that no vanilla effect uses (the mod's own assets)")
    if flags:
        out.append("COPIED FROM CHECKED LAYERS:")
        out += flags[:limit]
    elif against:
        out.append(f"  nothing identical to {', '.join(against)} that isn't also vanilla")
    out.append("")
    if effect and len(rows) <= 3:
        for rel, kind, n, new, col, ed, srcs, own_deps, copied, e, detail in rows:
            out.append(f"{rel}: {kind} ({n} components: {n - new - col - ed} unchanged, {col} recoloured, {ed} edited, {new} new)")
            for f, k in sorted(srcs.items(), key=lambda x: -x[1]):
                out.append(f"  from {f} ({k} components)")
            if own_deps:
                out.append(f"  non-vanilla resources: {', '.join(own_deps[:6])}")
            best = max(srcs, key=srcs.get) if srcs else None
            if best:
                out.append(f"  vs {best.split('/')[-1]}:")
                out += diff_parsed(parse(effect_lsx(cfg, "base", best)), e, limit)
        return "\n".join(out)
    for rel, kind, n, new, col, ed, srcs, own_deps, copied, e, detail in rows[:limit]:
        best = max(srcs, key=srcs.get).split("/")[-1] if srcs else "-"
        out.append(f"  {kind:24} {rel.split('/')[-1]}  <- {best}" + (f" +{len(srcs) - 1} more" if len(srcs) > 1 else "")
                   + (f"  [{new} new parts]" if new and kind != "new" else "") + ("  [own assets]" if own_deps else ""))
    if len(rows) > limit:
        out.append(f"  ... {len(rows) - limit} more (raise `limit` or pass `effect`)")
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------ colour search

def colour_of(store, mei_effects, active):
    """Dominant bucket of a MultiEffectInfo's effects from the fingerprint index (vanilla files only)."""
    vdb = _db()
    w, p = store._where(active)
    buckets = {}
    for e in mei_effects:
        r = store.db.execute(f"SELECT source_file FROM fx WHERE id=? AND {w} ORDER BY rank DESC LIMIT 1", [e.get("resource")] + p).fetchone()
        if not r:
            continue
        row = vdb.execute("SELECT bucket, ncomp FROM files WHERE path=?", (r[0],)).fetchone()
        if row and row[0] not in ("none",):
            buckets[row[0]] = buckets.get(row[0], 0) + (row[1] or 1)
    return max(buckets, key=buckets.get) if buckets else None


# ------------------------------------------------------------------------------------------------ lint

# fields holding effect (MultiEffectInfo / effect resource) GUIDs: *Effect, StatusEffectOnTurn, StatusEffectOverride(ForItems);
# not ManagedStatusEffectGroup, which names a status group
EFFECT_FIELD = re.compile(r"Effect(OnTurn|Override|OverrideForItems)?$")
GUID_ANY = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def lint(store, layer, active, limit=100):
    """Effect references of a mod layer: stats *Effect GUIDs that resolve nowhere, or only in a layer loaded after it (a mod
    it doesn't depend on), MultiEffectInfos pointing at unknown effect resources, and bank entries whose .lsfx is missing."""
    cfg = sources.load_config()
    if layer not in active:
        raise ValueError(f"{layer!r} isn't an active layer")
    below = active[: active.index(layer) + 1]
    w_all, p_all = store._where(active)
    w_b, p_b = store._where(below)

    def where(guid):
        if store.db.execute(f"SELECT 1 FROM mei WHERE uuid=? AND {w_b} LIMIT 1", [guid] + p_b).fetchone() or \
           store.db.execute(f"SELECT 1 FROM fx WHERE id=? AND {w_b} LIMIT 1", [guid] + p_b).fetchone():
            return "ok"
        r = store.db.execute(f"SELECT layer FROM mei WHERE uuid=? AND {w_all} UNION SELECT layer FROM fx WHERE id=? AND {w_all}",
                             [guid] + p_all + [guid] + p_all).fetchall()
        return ("only in " + ", ".join(sorted({x[0] for x in r}))) if r else "missing"

    problems, checked, seen = [], 0, {}
    for name, data in store.db.execute("SELECT name, data FROM stats WHERE layer=?", (layer,)):
        for k, v in json.loads(data).items():
            if not EFFECT_FIELD.search(k) or not isinstance(v, str):
                continue
            for g in GUID_ANY.findall(v.lower()):
                checked += 1
                st = seen.get(g) or seen.setdefault(g, where(g))
                if st != "ok":
                    problems.append(f"  {name}.{k}: {g} {st}")
    for uuid_, name, effects in store.db.execute("SELECT uuid, name, effects FROM mei WHERE layer=?", (layer,)):
        for e in json.loads(effects):
            g = (e.get("resource") or "").lower()
            if g:
                checked += 1
                st = seen.get(g) or seen.setdefault(g, where(g))
                if st != "ok":
                    problems.append(f"  MultiEffectInfo {name} ({uuid_}): effect {g} {st}")
    mod = next((m for m in cfg["mods"] if m["name"] == layer), None)
    if mod:
        root = sources.mod_root(cfg, mod)
        for rid, name, src in store.db.execute("SELECT id, name, source_file FROM fx WHERE layer=?", (layer,)):
            checked += 1
            if not os.path.exists(os.path.join(root, src)):
                problems.append(f"  bank entry {name} ({rid}): {src} is not in the mod")
    head = f"Effect lint for {layer} ({checked} reference(s) vs {', '.join(below)}): " + (f"{len(problems)} problem(s)" if problems else "clean")
    return "\n".join([head] + problems[:limit]
                     + ([f"  ... {len(problems) - limit} more"] if len(problems) > limit else []))


# ------------------------------------------------------------------------------------------------ recolour build

def _hex(s):
    s = s.lstrip("#")
    return [int(s[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def _target(pal):
    r, g, b = _hex(pal["color"])
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return h, min(1.0, s * float(pal.get("saturation", 1.0))), pal.get("mode", "hue"), float(pal.get("brightness", 1.0))


def recolour_text(text, pal, keep=KEEP_DEFAULT):
    """Rewrite every colour keyframe of an effect .lsx to the palette's hue; brightness (incl. HDR) and alpha are kept.
    mode 'hue': white/grey keys stay (the texture's colour shows); 'tint': they get the hue too. Lights always get the
    colour and their light-template override switched on. -> (text, stats)."""
    th, ts, mode, bright = _target(pal)
    out, prop, ctype = [], None, None
    st = {"keys": 0, "white_kept": 0, "lights": 0}
    for line in text.splitlines(keepends=True):
        if '<node id="EffectComponent"' in line:
            ctype, prop = None, None
        elif '<node id="Property"' in line:
            prop = None
        a = ATTR.search(line)
        if a:
            k, t, v = a.groups()
            if k == "Type" and t == "LSString" and prop is None:
                ctype = v
            elif k == "FullName":
                prop = v
            elif k == "Value" and t == "bool" and prop == LIGHT_OVERRIDE and v == "False":
                line = line.replace('value="False"', 'value="True"')
                st["lights"] += 1
            elif k == "Color" and t == "fvec4" and _is_colour_prop(prop) and prop not in keep and not prop.startswith("Color Grading"):
                r, g, b, al = (_rgb(v) + [1, 1, 1, 1])[:4]
                m = max(r, g, b)
                if m > 0:
                    h, s, _ = colorsys.rgb_to_hsv(r / m, g / m, b / m)
                    if s < 0.12 and mode != "tint" and ctype != "Light":
                        st["white_kept"] += 1
                    else:
                        # white/grey keys (tint, lights) get the full target saturation; coloured keys keep some of
                        # their own saturation contrast
                        nr, ng, nb = colorsys.hsv_to_rgb(th, ts if s < 0.12 else ts * (0.6 + 0.4 * s), 1.0)
                        new = " ".join(_fmt_f(x * m * bright) for x in (nr, ng, nb)) + " " + _fmt_f(al)
                        line = line.replace(f'value="{v}"', f'value="{new}"')
                        st["keys"] += 1
        out.append(line)
    return "".join(out), st


def _fmt_f(x):
    return f"{x:.7g}"


def _uid(*parts):
    return str(uuid.uuid5(NS, "|".join(parts)))


_banks = {}


def _base_bank_entry(cfg, rid):
    """The vanilla bank <node id="Resource"> text for an effect resource."""
    if not _banks:
        for f in glob.glob(os.path.join(sources.CACHE, "base", "*", "Public", "*", "Content", "**", "*.lsx"), recursive=True):
            t = open(f, encoding="utf-8-sig").read()
            if '<region id="EffectBank">' in t:
                _banks[f] = t
    for t in _banks.values():
        i = t.find(f'id="ID" type="FixedString" value="{rid}"')
        if i < 0:
            continue
        s = t.rfind('<node id="Resource">', 0, i)
        depth, j = 0, s
        for m in re.finditer(r"<node\b[^>]*?(/?)>|</node>", t[s:]):
            if m.group(0).startswith("</"):
                depth -= 1
            elif not m.group(1):
                depth += 1
            if depth == 0:
                j = s + m.end()
                break
        return t[s:j]
    return None


def _mei_file(cfg, store, uuid_, layer):
    if layer == "base":
        hits = glob.glob(os.path.join(sources.CACHE, "base", "*", "Public", "*", "MultiEffectInfos", uuid_ + ".lsx"))
    else:
        mod = next(m for m in cfg["mods"] if m["name"] == layer)
        hits = glob.glob(os.path.join(sources.mod_root(cfg, mod), "Public", "*", "MultiEffectInfos", uuid_ + ".ls*"))
    return hits[-1] if hits else None


BANK_HEAD = '<?xml version="1.0" encoding="utf-8"?>\n<save>\n\t<version major="4" minor="0" revision="9" build="0" />\n' \
            '\t<region id="EffectBank">\n\t\t<node id="EffectBank">\n\t\t\t<children>\n'
BANK_TAIL = '\t\t\t</children>\n\t\t</node>\n\t</region>\n</save>\n'


def build(store, layer, apply=False, log=print):
    """Recolour build from <mod>/Scripts/data/vfx.toml (see module doc). Dry run unless apply."""
    from . import toolkit
    cfg, m, pub, folder = toolkit._mod_dirs(layer)
    spec_path = os.path.join(m["path"], "Scripts", "data", "vfx.toml")
    if not os.path.exists(spec_path):
        return f"no spec at {spec_path}\n" + SPEC_HELP
    spec = tomllib.load(open(spec_path, "rb"))
    palettes = {**PRESETS, **spec.get("palettes", {})}
    allowed = ["base"] + list(spec.get("allow_sources", []))   # never another mod's effects unless listed (licences)
    active = store.active(None)
    tag = spec.get("bank", "VFX")
    bank_dir = os.path.join(pub, "Content", f"[PAK]_{tag}")
    gen_path = os.path.join(m["path"], "Scripts", "data", "vfx_generated.json")
    old = json.load(open(gen_path)) if os.path.exists(gen_path) else {}
    out, written, entries, generated, made = [f"VFX build for {layer} from {os.path.relpath(spec_path, m['path'])}"], [], [], {}, {}
    work = os.path.join(FX, "build", layer)
    shutil.rmtree(work, ignore_errors=True)
    for e in spec.get("effect", []):
        name, frm, pname = e["name"], e["from"], e.get("palette")
        if pname not in palettes:
            raise ValueError(f"{name}: unknown palette {pname!r} (presets: {', '.join(PRESETS)})")
        pal = palettes[pname]
        w, p = store._where(allowed)
        src = store.db.execute(f"SELECT uuid, name, effects, layer FROM mei WHERE {w} AND (uuid=? OR name=?) ORDER BY rank DESC LIMIT 1",
                               p + [frm.lower(), frm]).fetchone()
        if not src:
            raise ValueError(f"{name}: no MultiEffectInfo {frm!r} in {allowed} (other mods' effects need allow_sources)")
        mei_path = _mei_file(cfg, store, src[0], src[3])
        if not mei_path:
            raise FileNotFoundError(f"{name}: MultiEffectInfo file for {src[0]} not found")
        mapping = {}
        only = e.get("only")   # optional 1-based EffectInfo positions to recolour; others keep the original resource
        for i, info in enumerate(json.loads(src[2]), 1):
            rid = info.get("resource")
            if not rid or (only and i not in only):
                continue
            r = store.db.execute(f"SELECT layer, name, source_file FROM fx WHERE id=? AND {w} ORDER BY rank DESC LIMIT 1", [rid] + p).fetchone()
            if not r:
                out.append(f"  {name}: resource {rid} not indexed - left as is")
                continue
            key = (rid, pname)
            if key not in made:
                new_name = f"{r[1]}_{pname.capitalize()}"
                new_id = _uid(layer, "res", rid, pname)
                text, st = recolour_text(open(effect_lsx(cfg, r[0], r[2]), encoding="utf-8").read(), pal, tuple(e.get("keep", KEEP_DEFAULT)))
                rel = f"Public/{folder}/Assets/Effects/Effects_Banks/{e.get('folder', tag)}/{new_name}.lsfx"
                made[key] = (new_id, new_name, rel, text, st, r)
            mapping[rid] = made[key][0]
        if not mapping:
            out.append(f"  {name}: nothing to recolour")
            continue
        mei_uuid = _uid(layer, "mei", name)
        t = open(mei_path, encoding="utf-8-sig").read()
        # the MultiEffectInfo's own UUID/Name come before its first EffectInfo (top node "MultiEffectInfos" or, in older
        # files, "root"; attribute order varies)
        cut = t.find('<node id="EffectInfo"')
        head, rest = (t[:cut], t[cut:]) if cut >= 0 else (t, "")
        head = re.sub(r'(<attribute id="UUID" type="guid" value=")[^"]+', r"\g<1>" + mei_uuid, head, count=1)
        head = re.sub(r'(<attribute id="Name" type="LSString" value=")[^"]*', r"\g<1>" + name, head, count=1)
        t = head + rest
        t = re.sub(r'(<node id="EffectInfo">(?:(?!</?node)[\s\S])*?<attribute id="UUID" type="guid" value=")([^"]+)',
                   lambda mm: mm.group(1) + _uid(layer, "info", name, mm.group(2)), t)
        for o, nw in mapping.items():
            t = t.replace(f'value="{o}"', f'value="{nw}"')
        rel = f"Public/{folder}/MultiEffectInfos/{mei_uuid}.lsx"
        written.append((rel, t))
        generated[name] = {"uuid": mei_uuid, "from": f"{src[1]} ({src[0]})", "palette": pname,
                           "resources": {o: nw for o, nw in mapping.items()}}
        out.append(f"  {name} = {mei_uuid}  <- {src[1]} [{src[3]}], palette {pname}, {len(mapping)} effect(s) recoloured")
    for (rid, pname), (new_id, new_name, rel, text, st, r) in made.items():
        entry = _base_bank_entry(cfg, rid) if r[0] == "base" else None
        if not entry:
            raise RuntimeError(f"no bank entry found for {r[1]} ({rid}); only vanilla sources are supported for now")
        entry = re.sub(r'(id="ID" type="FixedString" value=")[^"]+', r"\g<1>" + new_id, entry, count=1)
        entry = re.sub(r'(id="(?:Name|EffectName)" type="(?:LSString|FixedString)" value=")[^"]+', r"\g<1>" + new_name, entry)
        entry = re.sub(r'(id="SourceFile" type="LSString" value=")[^"]+', r"\g<1>" + rel, entry, count=1)
        entries.append("\t\t\t\t" + entry.strip() + "\n")
        written.append((rel, text))
        out.append(f"    {new_name}: {st['keys']} colour keys, {st['white_kept']} white keys kept, {st['lights']} light override(s)")
    if not made:
        out.append("  nothing to build")
        return "\n".join(out)
    bank_rel = os.path.relpath(os.path.join(bank_dir, "_merged.lsf"), m["path"]).replace("\\", "/")
    written.append((bank_rel, BANK_HEAD + "".join(entries) + BANK_TAIL))
    files = [r for r, _ in written]
    stale = [f for f in old.get("_files", []) if f not in files]
    out.append(f"  {len(files)} file(s): {len(made)} effects, {len(generated)} MultiEffectInfos, 1 bank ({bank_rel})"
               + (f"; {len(stale)} stale file(s) from the last build to remove" if stale else ""))
    if not apply:
        out.append("dry run - pass apply=True to write")
        return "\n".join(out)
    ready = []   # everything is produced in the work dir first, so a failed conversion leaves the mod untouched
    for rel, text in written:
        tmp = os.path.join(work, rel + ".lsx")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        open(tmp, "w", encoding="utf-8").write(text)
        if rel.endswith((".lsfx", ".lsf")):   # binary resources: convert the .lsx
            conv = tmp[:-4] + ".out.lsf"
            sources.divine(cfg, "-a", "convert-resource", "-s", sources.winpath(tmp), "-d", sources.winpath(conv))
            ready.append((conv, rel))
        else:
            ready.append((tmp, rel))
    for src, rel in ready:
        dst = os.path.join(m["path"], rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
    for f in stale:
        p = os.path.join(m["path"], f)
        if os.path.exists(p):
            os.remove(p)
    generated["_files"] = files
    json.dump(generated, open(gen_path, "w"), indent=1)
    open(gen_path, "a").write("\n")
    shutil.rmtree(work, ignore_errors=True)
    out.append(f"written; MultiEffectInfo UUIDs in {os.path.relpath(gen_path, m['path'])}. Refresh the layer to index them.")
    return "\n".join(out)


SPEC_HELP = """Spec format (TOML):
  bank = "PTA_VFX"                 # Content/[PAK]_<bank>/_merged.lsf and Effects_Banks/<bank>/ (default VFX)
  allow_sources = []               # extra layers effects may be derived from (default: vanilla only)
  [palettes.mine]                  # optional custom palettes; presets: """ + ", ".join(PRESETS) + """
  color = "#8a0610"
  mode = "tint"                    # hue: keep white/grey keys (texture colour shows) | tint: colour them too
  saturation = 1.0                 # multiplier on the colour's saturation
  brightness = 1.0                 # multiplier on every recoloured key's brightness (HDR kept relative)
  [[effect]]
  name = "PTA_FingerOfDeath_BeamEffect"   # new MultiEffectInfo name
  from = "<vanilla MultiEffectInfo UUID or name>"
  palette = "blood"
  only = [1, 2]                    # optional: EffectInfo positions to recolour
  keep = ["Darken Environment.Color"]   # optional: colour properties left alone"""
