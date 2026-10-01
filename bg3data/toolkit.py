"""Larian Toolkit (Editor) export: mod.io publishing goes through the Toolkit, which edits its own copy of a
mod's data under Editor/Mods/<mod>/ (.stats for stats, .tbl for lists, progressions and other tables). A mod
built as game-ready files (Public/<mod>/Stats/Generated/Data/*.txt, *.lsx) needs that copy generated, and
both copies must stay in step ("every change is written twice").

Nothing about the editor formats is hard-coded beyond which file each data kind lives in:
  - field types (and enum attributes) are learned from the vanilla editor files in the game install plus
    every mod layer that ships an Editor/ folder;
  - how game-ready names/fields map to editor ones (spells drop their "<SpellType>_" prefix, MemoryCost is
    SpellPrepareCost, a progression's Name is FSName, ...) is learned by joining a mod's two copies
    (stats by name, tables by UUID) - dnd55e ships both;
  - existing editor UUIDs are kept, so re-exports produce small Toolkit diffs.
"""
import glob
import json
import os
import re
import time
import uuid
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

from . import parse, sources

NS = uuid.UUID("6f0b4b4e-6b1d-4b3a-9f5e-3b1f6f0e7a11")
SCHEMA_FILE = os.path.join(sources.CACHE, "toolkit_schema.json")
VANILLA_MODS = ("Shared", "SharedDev", "Gustav", "GustavDev", "GustavX")

# editor table file -> (game-ready source relative to Public/<mod>/, LSX node id)
TABLES = {
    "Lists/SpellLists.tbl": ("Lists/SpellLists.lsx", "SpellList"),
    "Lists/PassiveLists.tbl": ("Lists/PassiveLists.lsx", "PassiveList"),
    "Lists/SkillLists.tbl": ("Lists/SkillLists.lsx", "SkillList"),
    "Lists/AbilityLists.tbl": ("Lists/AbilityLists.lsx", "AbilityList"),
    "Lists/EquipmentLists.tbl": ("Lists/EquipmentLists.lsx", "EquipmentList"),
    "Progressions/Progressions.tbl": ("Progressions/Progressions.lsx", "Progression"),
    "ActionResourceDefinitions/ActionResourceDefinitions.tbl": ("ActionResourceDefinitions/ActionResourceDefinitions.lsx", "ActionResourceDefinition"),
    "ClassDescriptions/ClassDescriptions.tbl": ("ClassDescriptions/ClassDescriptions.lsx", "ClassDescription"),
    "Levelmaps/LevelMapValues.tbl": ("Levelmaps/LevelMapValues.lsx", "LevelMapSeries"),
    "Feats/Feats.tbl": ("Feats/Feats.lsx", "Feat"),
    "Feats/FeatDescriptions.tbl": ("Feats/FeatDescriptions.lsx", "FeatDescription"),
}
STATS_DIRS = ("Stats/SpellData", "Stats/StatusData", "Stats/Stats")
GEN_NAME = re.compile(r"^New_Stat_(\d+)$")


def stats_file(typ, fields):
    """Editor .stats file (relative to Editor/Mods/<mod>/) for a game-ready entry."""
    if typ == "SpellData":
        return f"Stats/SpellData/{fields.get('SpellType', 'Target')}.stats"
    if typ == "StatusData":
        return f"Stats/StatusData/Status_{fields.get('StatusType', 'BOOST')}.stats"
    kind = {"PassiveData": "Passive", "InterruptData": "Interrupt"}.get(typ, typ)
    return f"Stats/Stats/{kind}.stats"


def category(rel):
    return "SpellData" if "/SpellData/" in rel else "StatusData" if "/StatusData/" in rel else rel


def editor_name(typ, name, fields, rules):
    pref = f"{fields.get('SpellType', '')}_"
    if typ == "SpellData" and rules.get("spell_prefix", True) and name.startswith(pref):
        return name[len(pref):]
    return name


# ------------------------------------------------------------------ editor file I/O
def read_editor(path):
    """[(stat_object attrs, [field attrib dicts])], definition id, has BOM."""
    raw = open(path, "rb").read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    root = ET.fromstring(raw.decode("utf-8-sig"))
    objs = []
    for so in root.iter("stat_object"):
        fields = [dict(f.attrib) for f in so.iter("field")]
        objs.append((dict(so.attrib), fields))
    return objs, root.get("stat_object_definition_id"), bom


def write_editor(path, defid, objs, bom):
    out = ['<?xml version="1.0" encoding="utf-8"?>', f'<stats stat_object_definition_id="{defid}">', "  <stat_objects>"]
    for so_attrs, fields in objs:
        attrs = " ".join(f"{k}={quoteattr(v)}" for k, v in so_attrs.items())
        out += [f"    <stat_object {attrs}>", "      <fields>"]
        for f in fields:
            out.append("        <field " + " ".join(f"{k}={quoteattr(str(v))}" for k, v in f.items()) + " />")
        out += ["      </fields>", "    </stat_object>"]
    out += ["  </stat_objects>", "</stats>"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig" if bom else "utf-8", newline="\r\n") as fh:
        fh.write("\n".join(out) + "\n")


def _fval(f):
    """Comparable value of an editor field."""
    if "handle" in f:
        return f"{f['handle']};{f.get('version', '1')}"
    return f.get("value", "")


# ------------------------------------------------------------------ learning
def _editor_roots(cfg):
    roots = [os.path.join(cfg["base"]["game_data"], "Editor", "Mods", m) for m in VANILLA_MODS]
    for m in cfg["mods"]:
        if not m["path"].lower().endswith(".pak"):
            roots += glob.glob(os.path.join(m["path"], "Editor", "Mods", "*"))
    return [r for r in roots if os.path.isdir(r)]


def _editor_files(root):
    files = []
    for d in STATS_DIRS:
        files += glob.glob(os.path.join(root, d, "*.stats"))
    files += [os.path.join(root, t) for t in TABLES if os.path.exists(os.path.join(root, t))]
    return files


def learn(cfg, force=False):
    """Field types per editor file, definition ids, BOMs, vanilla/dependency name->UUID maps, and the
    game-ready -> editor mappings learned from mods that ship both copies. Cached; rebuilt on change."""
    roots = _editor_roots(cfg)
    stamp = max([os.path.getmtime(f) for r in roots for f in _editor_files(r)] or [0])
    if not force and os.path.exists(SCHEMA_FILE):
        cached = json.load(open(SCHEMA_FILE))
        if cached.get("stamp") == stamp and cached.get("roots") == roots:
            return cached
    files, uuids = {}, {}
    for root in roots:
        for path in _editor_files(root):
            rel = os.path.relpath(path, root).replace("\\", "/")
            objs, defid, bom = read_editor(path)
            info = files.setdefault(rel, {"defid": defid, "bom": bom, "types": {}, "head": None})
            for _, fields in objs:
                if info["head"] is None and len(fields) >= 2:
                    info["head"] = [fields[0]["name"], fields[1]["name"]]
                for f in fields:
                    t = {k: v for k, v in f.items() if k not in ("name", "value", "handle", "clear_inherited_value")}
                    info["types"].setdefault(f["name"], t)
                if rel.startswith("Stats/"):
                    nm = next((f.get("value") for f in fields if f["name"] == "Name"), None)
                    uid = next((f.get("value") for f in fields if f["name"] == "UUID"), None)
                    if nm and uid:
                        uuids.setdefault(f"{rel}|{nm}", uid)
    rules = _learn_rules(cfg)
    # a field is only "dropped" if no editor file of that kind ever has it (otherwise it's drift between copies)
    known = {}
    for rel, info in files.items():
        known.setdefault(category(rel), set()).update(info["types"])
    cat_of = {"SpellData": "SpellData", "StatusData": "StatusData"}
    rules["stats_drop"] = [k for k in rules["stats_drop"]
                           if k.split("|", 1)[1] not in known.get(cat_of.get(k.split("|")[0], stats_file(k.split("|")[0], {})), set())]
    schema = {"stamp": stamp, "roots": roots, "files": files, "uuids": uuids, "rules": rules}
    os.makedirs(os.path.dirname(SCHEMA_FILE), exist_ok=True)
    json.dump(schema, open(SCHEMA_FILE, "w"))
    return schema


def _pairs(cfg):
    """Mod layers that ship both copies: (Public/<mod> dir, Editor/Mods/<mod> dir)."""
    for m in cfg["mods"]:
        if m["path"].lower().endswith(".pak"):
            continue
        for ed in glob.glob(os.path.join(m["path"], "Editor", "Mods", "*")):
            pub = os.path.join(m["path"], "Public", os.path.basename(ed))
            if os.path.isdir(pub) and _editor_files(ed):
                yield pub, ed


def _learn_rules(cfg):
    """Learn renames: for stats, game-ready field -> editor field; for tables, editor field -> LSX attribute.
    A pair counts when the values are equal; the majority wins. Fields present only on the game-ready side
    are recorded as dropped (e.g. SpellType, implied by the file)."""
    stats_votes, drop_votes, table_votes, gen_name, value_votes = {}, {}, {}, {}, {}
    prefix_hits = prefix_total = 0
    for pub, ed in _pairs(cfg):
        editor = {}
        for path in glob.glob(os.path.join(ed, "Stats", "*", "*.stats")):
            rel = os.path.relpath(path, ed).replace("\\", "/")
            for _, fields in read_editor(path)[0]:
                fd = {f["name"]: _fval(f) for f in fields}
                if fd.get("Name"):
                    editor[(rel, fd["Name"])] = fd
        for path in glob.glob(os.path.join(pub, "Stats", "Generated", "Data", "*.txt")):
            for name, typ, using, data in parse.parse_stats(parse.read_text(path)):
                rel = stats_file(typ, data)
                short = editor_name(typ, name, data, {"spell_prefix": True})
                ed_fields = editor.get((rel, short))
                if typ == "SpellData":
                    prefix_total += 1
                    if ed_fields is not None:
                        prefix_hits += 1
                    elif (rel, name) in editor:
                        ed_fields = editor[(rel, name)]
                if ed_fields is None:
                    continue
                for k, v in data.items():
                    if k in ed_fields:
                        continue
                    target = next((ek for ek, ev in ed_fields.items() if ev == v and v not in ("", None)
                                   and ek not in data and ek not in ("UUID", "Name", "Using")), None)
                    key = f"{typ}|{k}"
                    if target:
                        stats_votes.setdefault(key, {}).setdefault(target, 0)
                        stats_votes[key][target] += 1
                    else:
                        drop_votes[key] = drop_votes.get(key, 0) + 1
        for tbl, (src, node) in TABLES.items():
            tpath, spath = os.path.join(ed, tbl), os.path.join(pub, src)
            if not (os.path.exists(tpath) and os.path.exists(spath)):
                continue
            nodes = {a.get("UUID"): a for a in parse.parse_nodes(spath, node) if a.get("UUID")}
            for _, fields in read_editor(tpath)[0]:
                fd = {f["name"]: _fval(f) for f in fields}
                a = nodes.get(fd.get("UUID"))
                if not a:
                    continue
                if GEN_NAME.match(fd.get("Name", "")):
                    gen_name[tbl] = gen_name.get(tbl, 0) + 1
                for ek, ev in fd.items():
                    srcs = [k for k, v in a.items() if _norm(v) == _norm(ev) and ev not in ("", None)]
                    if not srcs and ek in a and ev not in ("", None):  # same field, different encoding
                        value_votes.setdefault(f"{tbl}|{ek}", {}).setdefault(str(a[ek]), {}).setdefault(ev, 0)
                        value_votes[f"{tbl}|{ek}"][str(a[ek])][ev] += 1
                    if srcs:
                        best = ek if ek in srcs else srcs[0]
                        table_votes.setdefault(f"{tbl}|{ek}", {}).setdefault(best, 0)
                        table_votes[f"{tbl}|{ek}"][best] += 1
    pick = lambda votes: {k: max(v, key=v.get) for k, v in votes.items()}
    stats_ren = pick(stats_votes)
    return {
        "spell_prefix": prefix_total == 0 or prefix_hits >= prefix_total / 2,
        "stats_rename": stats_ren,
        "stats_drop": sorted(k for k, n in drop_votes.items() if k not in stats_ren and n >= 5),
        "table_map": pick(table_votes),
        "generated_name": sorted(gen_name),
        "table_values": _encodings(value_votes),
    }


def _encodings(value_votes):
    """Keep only real encodings (numeric game value -> editor word, e.g. PrimaryAbility 4 -> Intelligence)
    that are consistent; anything else is drift between a mod's two copies, not a format rule."""
    out = {}
    for key, m in value_votes.items():
        if key.endswith("|Name"):
            continue
        good = {}
        for sv, evs in m.items():
            top = max(evs, key=evs.get)
            numeric_top = re.fullmatch(r"-?[0-9.]+", top)
            same_number = numeric_top and abs(float(sv) - float(top)) < 1e-6 if re.fullmatch(r"-?[0-9.]+", sv) else False
            if re.fullmatch(r"-?[0-9.]+", sv) and (not numeric_top or same_number) and evs[top] >= 0.9 * sum(evs.values()):
                good[sv] = top
        if good and len(good) >= 0.8 * len(m):
            out[key] = good
    return out


GUID_LABEL = re.compile(r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\(.*\)$", re.I)


def _norm(v):
    s = str(v if v is not None else "").strip()
    if s.lower() in ("true", "false"):
        return s.lower()
    return s.rstrip(";")


# ------------------------------------------------------------------ export
def _mod_dirs(layer):
    cfg = sources.load_config()
    m = next((x for x in cfg["mods"] if x["name"] == layer), None)
    if not m:
        raise ValueError(f"unknown mod layer {layer!r}")
    if m["path"].lower().endswith(".pak"):
        raise ValueError("Toolkit export needs a folder layer (the mod's source), not a .pak")
    pubs = glob.glob(os.path.join(m["path"], "Public", "*"))
    pubs = [p for p in pubs if os.path.isdir(p)]
    if len(pubs) != 1:
        raise ValueError(f"expected one Public/<mod> folder in {m['path']}, found {len(pubs)}")
    return cfg, m, pubs[0], os.path.basename(pubs[0])


def editor_root(layer, dest="toolkit"):
    cfg, m, pub, folder = _mod_dirs(layer)
    if dest == "repo":
        return os.path.join(m["path"], "Editor", "Mods", folder)
    return os.path.join(cfg["base"]["game_data"], "Editor", "Mods", folder)


def _convert_field(name, value, ftype, clear=False):
    f = {"name": name, "type": ftype.get("type", "StringTableFieldDefinition")}
    if f["type"] == "TranslatedStringTableFieldDefinition" and value and ";" in value and value.startswith("h"):
        h, v = value.split(";", 1)
        f.update(handle=h, version=v or "1")
        return f
    if clear or value == "":
        f["clear_inherited_value"] = "true"
    m = GUID_LABEL.match(str(value))
    if m:  # game files write "uuid(AnimationName)"; the editor stores the uuid
        value = m.group(1)
    if f["type"] == "BoolTableFieldDefinition":
        value = "True" if str(value).lower() in ("true", "1", "yes") else "False" if value != "" else ""
    f["value"] = value
    for k, v in ftype.items():
        if k not in ("type",):
            f[k] = v
    return f


def build(store, active, layer, dest="toolkit"):
    """In-memory editor files for a layer: {rel: (defid, bom, [(so_attrs, fields)])} plus warnings."""
    cfg, m, pub, folder = _mod_dirs(layer)
    schema = learn(cfg)
    rules, files_schema = schema["rules"], schema["files"]
    out_root = editor_root(layer, dest)
    existing = {}
    for path in _editor_files(out_root) if os.path.isdir(out_root) else []:
        rel = os.path.relpath(path, out_root).replace("\\", "/")
        existing[rel] = read_editor(path)[0]
    warnings, out = [], {}

    def ftype(rel, name):
        t = files_schema.get(rel, {}).get("types", {}).get(name)
        if t is None and re.search(r"\d+$", name):  # Level13 -> same type as Level1
            base = re.sub(r"\d+$", "", name)
            t = next((v for k, v in files_schema.get(rel, {}).get("types", {}).items() if re.sub(r"\d+$", "", k) == base), None)
        if t is None:
            # same field in a sibling file of the same kind
            for r2, info in files_schema.items():
                if category(r2) == category(rel) and name in info["types"]:
                    return info["types"][name]
            warnings.append(f"{rel}: no known editor type for field '{name}' (written as a string)")
            return {"type": "StringTableFieldDefinition"}
        return t

    # ---- stats
    entries = []
    for path in sorted(glob.glob(os.path.join(pub, "Stats", "Generated", "Data", "*.txt"))):
        if os.path.basename(path).lower() in ("data.txt", "xpdata.txt"):
            continue  # ExtraData/XP tables: not stat objects
        for name, typ, using, data in parse.parse_stats(parse.read_text(path)):
            if typ in ("SpellData", "StatusData") and ("SpellType" if typ == "SpellData" else "StatusType") not in data:
                r = store.resolve(name, active)  # inherited type decides the editor file
                key = "SpellType" if typ == "SpellData" else "StatusType"
                if r and r["fields"].get(key):
                    data = {key: r["fields"][key][0], **data}
            entries.append((name, typ, using, data))
    own, by_full = {}, {}  # (rel|editor name) -> uuid; game-ready name -> (rel, editor name)
    olds = {}
    for rel, objs in existing.items():
        d = olds.setdefault(rel, {})
        for _, fl in objs:
            nm = next((f.get("value") for f in fl if f["name"] == "Name"), None)
            uid = next((f.get("value") for f in fl if f["name"] == "UUID"), None)
            if nm and uid:
                d.setdefault(nm, []).append(uid)
    seen = {}
    for name, typ, using, data in entries:
        rel = stats_file(typ, data)
        ename = editor_name(typ, name, data, rules)
        n = seen[(rel, ename)] = seen.get((rel, ename), -1) + 1  # duplicate names (separators) keep their order
        prev = olds.get(rel, {}).get(ename, [])
        uid = prev[n] if n < len(prev) else str(uuid.uuid5(NS, f"{folder}|{rel}|{ename}|{n}"))
        own.setdefault(f"{rel}|{ename}", uid)
        by_full.setdefault(name, (rel, ename))
    seen = {}
    for name, typ, using, data in entries:
        rel = stats_file(typ, data)
        ename = editor_name(typ, name, data, rules)
        n = seen[(rel, ename)] = seen.get((rel, ename), -1) + 1
        prev = olds.get(rel, {}).get(ename, [])
        my_uid = prev[n] if n < len(prev) else str(uuid.uuid5(NS, f"{folder}|{rel}|{ename}|{n}"))
        fields = [{"name": "UUID", "type": "IdTableFieldDefinition", "value": my_uid},
                  {"name": "Name", "type": "NameTableFieldDefinition", "value": ename}]
        if using:
            # the parent's file: a spell's parent lives in the file of ITS SpellType (from its name prefix)
            if typ == "SpellData" and "_" in using:
                prel = f"Stats/SpellData/{using.split('_', 1)[0]}.stats"
                pname = using.split("_", 1)[1] if rules.get("spell_prefix", True) else using
            else:
                prel, pname = rel, using
            if using in by_full and using != name:
                prel, pname = by_full[using]
            key = f"{prel}|{pname}"
            # overriding an entry of the same name: the parent is the lower layer's object, not ours
            puid = schema["uuids"].get(key) if using == name else (own.get(key) or schema["uuids"].get(key))
            if not puid and typ == "StatusData":  # a status may inherit from another status type
                for k2, u2 in list(own.items()) + list(schema["uuids"].items()):
                    if k2.startswith("Stats/StatusData/") and k2.endswith("|" + using) and (using != name or k2 in schema["uuids"]):
                        puid = u2
                        break
            if puid:
                fields.append({"name": "Using", "type": "BaseClassTableFieldDefinition", "value": puid})
            else:
                warnings.append(f"{rel}: {ename} uses '{using}', which isn't in this mod, its dependencies' or the vanilla editor data")
        for k, v in data.items():
            if f"{typ}|{k}" in rules["stats_drop"]:
                continue
            ek = rules["stats_rename"].get(f"{typ}|{k}", k)
            fields.append(_convert_field(ek, v, ftype(rel, ek), clear=(v == "" and bool(using))))
        out.setdefault(rel, []).append(({"is_substat": "false"}, fields))
    # ---- tables
    for tbl, (src, node) in TABLES.items():
        spath = os.path.join(pub, src)
        if not os.path.exists(spath):
            continue
        tmap = {k.split("|", 1)[1]: v for k, v in rules["table_map"].items() if k.startswith(tbl + "|")}
        inv = {}
        for ek, sk in tmap.items():
            inv.setdefault(sk, ek)
        olds = {next((f.get("value") for f in fl if f["name"] == "UUID"), None): fl for _, fl in existing.get(tbl, [])}
        counter = max([int(GEN_NAME.match(f.get("value", "")).group(1)) for fl in olds.values() for f in fl
                       if f["name"] == "Name" and GEN_NAME.match(f.get("value", ""))] or [0])
        objs = []
        for a in parse.parse_nodes(spath, node):
            uid = a.get("UUID")
            fields = [{"name": "UUID", "type": "IdTableFieldDefinition", "value": uid}]
            if tbl in rules["generated_name"]:
                old_name = next((f.get("value") for f in olds.get(uid, []) if f["name"] == "Name"), None)
                if not old_name:
                    counter += 1
                    old_name = f"New_Stat_{counter}"
                fields.insert(0, {"name": "Name", "type": "NameTableFieldDefinition", "value": old_name})
            for k, v in a.items():
                if k in ("UUID", "Comment"):
                    continue  # Comment: LSX-only note, not an editor field
                ek = inv.get(k, k[1:] if k.startswith("_") else k)
                if ek == "Name" and tbl in rules["generated_name"]:
                    ek = inv.get("Name", "FSName")
                v = rules.get("table_values", {}).get(f"{tbl}|{ek}", {}).get(str(v), v)
                fields.append(_convert_field(ek, v, ftype(tbl, ek)))
            objs.append(({"is_substat": "false"}, fields))
        out[tbl] = objs
    files = {rel: (files_schema.get(rel, {}).get("defid") or _defid_for(files_schema, rel), files_schema.get(rel, {}).get("bom", rel.endswith(".stats")), objs)
             for rel, objs in out.items()}
    return files, sorted(set(warnings)), out_root


def _defid_for(files_schema, rel):
    for r, info in files_schema.items():
        if category(r) == category(rel) and info.get("defid"):
            return info["defid"]
    return ""


def export(store, active, layer, dest="toolkit", dry_run=True):
    files, warnings, root = build(store, active, layer, dest)
    lines = [f"Toolkit export of {layer} -> {root}" + (" (dry run, nothing written)" if dry_run else "")]
    for rel, (defid, bom, objs) in sorted(files.items()):
        lines.append(f"  {rel}: {len(objs)} objects")
        if not dry_run:
            write_editor(os.path.join(root, rel), defid, objs, bom)
    if warnings:
        lines.append(f"warnings ({len(warnings)}):")
        lines += [f"  {w}" for w in warnings[:60]] + ([f"  ... {len(warnings) - 60} more"] if len(warnings) > 60 else [])
    return "\n".join(lines)


def check(store, active, layer, dest="toolkit", limit=80):
    """Compare the editor copy at `dest` with what an export of the game-ready files would produce."""
    files, warnings, root = build(store, active, layer, dest)
    names_by_uuid = {u: k.split("|", 1)[1] for k, u in learn(sources.load_config())["uuids"].items()}
    lines, problems = [f"Toolkit sync check for {layer}: game-ready files vs {root}"], 0
    total = sum(len(o) for _, _, o in files.values())
    for rel, (_, _, objs) in sorted(files.items()):
        path = os.path.join(root, rel)
        want = {(next(f["value"] for f in fl if f["name"] == "UUID")): fl for _, fl in objs}
        if not os.path.exists(path):
            lines.append(f"  MISSING FILE {rel} ({len(want)} objects)")
            problems += len(want)
            continue
        have = {}
        for _, fl in read_editor(path)[0]:
            u = next((f.get("value") for f in fl if f["name"] == "UUID"), None)
            if u:
                have[u] = fl
        name = lambda fl: next((f.get("value") for f in fl if f["name"] in ("FSName", "NameFS", "Name")), "?")
        uname = dict(names_by_uuid)
        for fl in have.values():
            uname.setdefault(next((f.get("value") for f in fl if f["name"] == "UUID"), ""), name(fl))
        for _, fl in objs:
            uname.setdefault(next((f.get("value") for f in fl if f["name"] == "UUID"), ""), name(fl))
        def comparable(fl):
            out = {}
            for f in fl:
                if f["name"] == "Name" and GEN_NAME.match(f.get("value", "")):
                    continue
                v = _norm(_fval(f))
                out[f["name"]] = uname.get(v, v) if f["name"] == "Using" else v
            return out
        missing = [u for u in want if u not in have]
        extra = [u for u in have if u not in want]
        changed = []
        for u in want:
            if u in have:
                a, b = comparable(want[u]), comparable(have[u])
                diff = sorted(k for k in set(a) | set(b) if a.get(k, "") != b.get(k, ""))
                if diff:
                    changed.append((name(want[u]), diff))
        problems += len(missing) + len(extra) + len(changed)
        if missing or extra or changed:
            lines.append(f"  {rel}: {len(missing)} missing, {len(extra)} only in editor, {len(changed)} differ")
            lines += [f"    missing: {name(want[u])}" for u in missing[:5]]
            lines += [f"    only in editor: {name(have[u])}" for u in extra[:5]]
            lines += [f"    differs: {n} ({', '.join(d[:6])})" for n, d in changed[:5]]
    lines.insert(1, f"  {problems} problem(s) in {total} objects across {len(files)} file(s)" + (" - in sync" if not problems else ""))
    if warnings:
        lines.append(f"export warnings: {len(warnings)} (see bg3_toolkit_export)")
    return "\n".join(lines[:limit + 2])


def status(layer):
    """Where the Toolkit expects this mod, and whether those folders are the repo (junctions) or copies."""
    cfg, m, pub, folder = _mod_dirs(layer)
    data = cfg["base"]["game_data"]
    lines = [f"Toolkit status for {layer} ({folder})"]
    for kind, path, repo in (("Projects", os.path.join(data, "Projects", folder), os.path.join(m["path"], "Projects", folder)),
                             ("Mods", os.path.join(data, "Mods", folder), os.path.join(m["path"], "Mods", folder)),
                             ("Public", os.path.join(data, "Public", folder), os.path.join(m["path"], "Public", folder)),
                             ("Editor", os.path.join(data, "Editor", "Mods", folder), os.path.join(m["path"], "Editor", "Mods", folder))):
        if not os.path.exists(path):
            lines.append(f"  {kind:8} MISSING   {path}")
            continue
        same = os.path.realpath(path) == os.path.realpath(repo) if os.path.exists(repo) else False
        nfiles = sum(len(f) for _, _, f in os.walk(path))
        lines.append(f"  {kind:8} {'-> repo ' if same else 'copy    '} {nfiles} files  {path}")
    meta = os.path.join(m["path"], "Mods", folder, "meta.lsx")
    if os.path.exists(meta):
        t = open(meta, encoding="utf-8").read()
        ver = re.search(r'id="Version64" type="int64" value="(\d+)"', t)
        if ver:
            v = int(ver.group(1))
            lines.append(f"  meta.lsx version {v >> 55}.{(v >> 47) & 0xFF}.{(v >> 31) & 0xFFFF}.{v & 0x7FFFFFFF}")
    return "\n".join(lines)
