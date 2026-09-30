"""Layered queries over the index.

Layer selection: `layers=None` means every configured layer; otherwise a list of mod-layer
names to stack on top of `base` (base is always included), e.g. ["dnd55e"].

Stats resolution follows BG3 semantics:
  * the highest-ranked `new entry NAME` among the active layers wins;
  * `using "NAME"` (self) inherits the previous definition of NAME in lower layers;
  * `using "OTHER"` inherits OTHER resolved across all active layers;
  * no `using` -> the entry stands alone (a redefinition without `using` replaces).
Every resolved field records the source that set it.
"""
import json
import re

from . import index, sources

GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def like(text):
    """Escape LIKE wildcards so user input matches literally (use with ESCAPE '\\')."""
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


VISUAL_KEYS = re.compile(r"(Effect|Animation|Sound|Icon|Trajector|VerbalIntent|StyleGroup|Beam|Hit.*Type|Sheathing)", re.I)
HANDLE = re.compile(r"^h[0-9a-z]{8}g[0-9a-z]{4}g[0-9a-z]{4}g[0-9a-z]{4}g[0-9a-z]{12}$|^h[0-9a-z]{20,40}$")


class Store:
    def __init__(self, refresh=True, log=print):
        self.db = index.connect()
        self.cfg = sources.load_config()
        self._cache = {}
        if refresh:
            index.refresh(self.db, self.cfg, log=log)

    def invalidate(self):
        self._cache.clear()

    # ------------------------------------------------------------ layers
    def layer_rows(self):
        return self.db.execute("SELECT name, kind, path, order_idx, newest, indexed_at, counts FROM layers ORDER BY order_idx").fetchall()

    def active(self, layers):
        names = [r[0] for r in self.layer_rows()]
        if layers is None:
            return names
        bad = [l for l in layers if l not in names]
        if bad:
            raise ValueError(f"unknown layer(s) {bad}; known: {names}")
        return ["base"] + [n for n in names if n in layers and n != "base"]

    def _where(self, active):
        return "layer IN (%s)" % ",".join("?" * len(active)), list(active)

    # ------------------------------------------------------------ stats
    def defs(self, name, active):
        w, p = self._where(active)
        return self.db.execute(f"SELECT rank, layer, source, file, type, using_, data FROM stats WHERE name=? AND {w} ORDER BY rank",
                               [name] + p).fetchall()

    def resolve(self, name, active, below_rank=None, _seen=None):
        """Return {'name','type','source','file','chain':[...], 'fields':{k:(value, source)}} or None."""
        ck = (name, tuple(active), below_rank)
        if _seen is None and ck in self._cache:
            return self._cache[ck]
        r = self._resolve(name, active, below_rank, _seen)
        if _seen is None:
            if len(self._cache) > 200_000:
                self._cache.clear()
            self._cache[ck] = r
        return r

    def _resolve(self, name, active, below_rank=None, _seen=None):
        _seen = _seen or set()
        key = (name, below_rank)
        if key in _seen:
            return None
        _seen.add(key)
        rows = [r for r in self.defs(name, active) if below_rank is None or r[0] < below_rank]
        if not rows:
            return None
        rank, layer, source, file, typ, using, data = rows[-1]
        parent = None
        if using == name:
            parent = self.resolve(name, active, below_rank=rank, _seen=_seen)
        elif using:
            parent = self.resolve(using, active, _seen=_seen)
        fields = dict(parent["fields"]) if parent else {}
        for k, v in json.loads(data).items():
            fields[k] = (v, source)
        link = f"{name} @ {source} ({file})" + (f" using {using}" + ("" if parent else " [UNRESOLVED]") if using else "")
        return {"name": name, "type": typ or (parent or {}).get("type"), "source": source, "file": file,
                "chain": [link] + (parent["chain"] if parent else []), "fields": fields}

    def search_stats(self, text, active, type_=None, field=None, limit=50):
        w, p = self._where(active)
        sql = f"SELECT DISTINCT name, type, layer FROM stats WHERE {w}"
        args = list(p)
        if type_:
            sql += " AND type=?"
            args.append(type_)
        if field:
            sql += " AND json_extract(data, ?) LIKE ? ESCAPE '\\'"
            args += ['$."' + field.replace('"', '') + '"', like(text)]
        else:
            sql += " AND (name LIKE ? ESCAPE '\\' OR data LIKE ? ESCAPE '\\')"
            args += [like(text), like(text)]
        sql += " ORDER BY name LIMIT ?"
        args.append(limit * 4)
        seen, out = set(), []
        for name, typ, layer in self.db.execute(sql, args):
            if name in seen:
                continue
            seen.add(name)
            out.append((name, typ, layer))
            if len(out) >= limit:
                break
        return out

    def references(self, token, active, limit=100):
        w, p = self._where(active)
        pat = like(token)
        out = []
        for t, cols in (("stats", "name, layer, source"), ("templates", "coalesce(name, mapkey), layer, source"),
                        ("prog", "name || ' L' || level || ' ' || uuid, layer, source"),
                        ("lists", "coalesce(name, uuid), layer, source")):
            col = "data" if t == "stats" else "attrs"
            name_col = "name" if t != "prog" else "uuid"
            for row in self.db.execute(f"SELECT {cols} FROM {t} WHERE {w} AND ({col} LIKE ? ESCAPE '\\' OR {name_col}=?) LIMIT ?",
                                       p + [pat, token, limit]):
                out.append((t,) + row)
        for row in self.db.execute(f"SELECT name, layer, source FROM mei WHERE {w} AND (uuid=? OR effects LIKE ? ESCAPE '\\') LIMIT ?",
                                   p + [token, pat, limit]):
            out.append(("effect",) + row)
        return out

    def diff(self, name, layer, active):
        """Fields `layer` changes for NAME relative to the stack below it."""
        idx = active.index(layer)
        before = self.resolve(name, active[:idx]) if idx > 0 else None
        after = self.resolve(name, active[: idx + 1])
        bf = before["fields"] if before else {}
        af = after["fields"] if after else {}
        changes = []
        for k in sorted(set(bf) | set(af)):
            a, b = af.get(k, (None,))[0], bf.get(k, (None,))[0]
            if a != b:
                changes.append((k, b, a))
        return before, after, changes

    # ------------------------------------------------------------ loca
    def loca(self, handle, active):
        w, p = self._where(active)
        h = handle.split(";")[0]
        r = self.db.execute(f"SELECT text, source, version FROM loca WHERE handle=? AND {w} ORDER BY rank DESC LIMIT 1", [h] + p).fetchone()
        return r

    def loca_search(self, text, active, limit=30):
        w, p = self._where(active)
        return self.db.execute(f"SELECT handle, text, source FROM loca WHERE {w} AND text LIKE ? ESCAPE '\\' LIMIT ?", p + [like(text), limit]).fetchall()

    def display_name(self, fields, active):
        h = fields.get("DisplayName", ("",))[0]
        if not h:
            return ""
        r = self.loca(h, active)
        return r[0] if r else ""

    # ------------------------------------------------------------ templates
    def template(self, key, active):
        w, p = self._where(active)
        r = self.db.execute(f"SELECT mapkey FROM templates WHERE (mapkey=? OR name=?) AND {w} ORDER BY rank DESC LIMIT 1", [key, key] + p).fetchone()
        if not r:
            return None
        chain, merged, seen, mk = [], {}, set(), r[0]
        while mk and mk not in seen:
            seen.add(mk)
            row = self.db.execute(f"SELECT name, parent, source, attrs FROM templates WHERE mapkey=? AND {w} ORDER BY rank DESC LIMIT 1", [mk] + p).fetchone()
            if not row:
                chain.append(f"{mk} [NOT FOUND]")
                break
            name, parent, source, attrs = row
            chain.append(f"{name} ({mk}) @ {source}")
            for k, v in json.loads(attrs).items():
                if k not in merged and v not in (None, ""):
                    merged[k] = (v, f"{name} @ {source}")
            mk = parent
        return {"chain": chain, "fields": merged}

    # ------------------------------------------------------------ progressions / lists
    def progression(self, key, active, level=None):
        """Nodes for a progression table (by TableUUID or Name), merged by node UUID (higher layer wins)."""
        w, p = self._where(active)
        rows = self.db.execute(f"SELECT uuid, table_uuid, name, level, source, attrs, rank FROM prog WHERE (table_uuid=? OR name=?) AND {w} ORDER BY rank",
                               [key, key] + p).fetchall()
        merged = {}
        for uuid, table, name, lvl, source, attrs, rank in rows:
            merged[uuid or f"_{rank}"] = (lvl, name, table, source, json.loads(attrs))
        out = sorted(merged.values(), key=lambda t: (t[0], t[1] or ""))
        return [o for o in out if level is None or o[0] == level]

    def spell_list(self, key, active):
        w, p = self._where(active)
        row = self.db.execute(f"SELECT node, uuid, name, source, attrs FROM lists WHERE (uuid=? OR name=?) AND {w} ORDER BY rank DESC LIMIT 1", [key, key] + p).fetchone()
        return row

    # ------------------------------------------------------------ spells
    def visual_kit(self, fields):
        return {k: v for k, (v, _) in fields.items() if VISUAL_KEYS.search(k) and v}

    def similar_spells(self, active, damage_type=None, school=None, spell_type=None, level=None, keyword=None, limit=25):
        w, p = self._where(active)
        names = [r[0] for r in self.db.execute(f"SELECT DISTINCT name FROM stats WHERE type='SpellData' AND {w}", p)]
        kw = keyword.lower() if keyword else None
        out, seen_names = [], set()
        for n in sorted(names):
            if spell_type and not n.startswith(spell_type + "_"):
                continue
            if kw and kw not in n.lower():
                continue
            r = self.resolve(n, active)
            if not r:
                continue
            f = {k: v for k, (v, _) in r["fields"].items()}
            if f.get("RootSpellID") and f["RootSpellID"] != n:
                continue  # upcast tier of another spell: same visuals
            if damage_type and damage_type.lower() not in (f.get("DamageType", "") + f.get("SpellSuccess", "") + f.get("SpellProperties", "")).lower():
                continue
            if school and school.lower() != f.get("SpellSchool", "").lower():
                continue
            if level is not None and str(level) != f.get("Level", ""):
                continue
            kit = self.visual_kit(r["fields"])
            if not any("Effect" in k for k in kit):
                continue
            dn = self.display_name(r["fields"], active)
            if dn and dn in seen_names:
                continue
            seen_names.add(dn)
            out.append((n, dn, f.get("Level", ""), f.get("SpellSchool", ""), kit))
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------ effects (phase 2)
    def fx_resource(self, rid, active):
        w, p = self._where(active)
        return self.db.execute(f"SELECT name, duration, looping, source_file, source FROM fx WHERE id=? AND {w} ORDER BY rank DESC LIMIT 1",
                               [rid] + p).fetchone()

    def effect(self, guid, active):
        """Resolve a MultiEffectInfo GUID (what stats *Effect fields hold) or an effect resource GUID."""
        w, p = self._where(active)
        row = self.db.execute(f"SELECT uuid, name, effects, source FROM mei WHERE uuid=? AND {w} ORDER BY rank DESC LIMIT 1",
                              [guid] + p).fetchone()
        if row:
            uuid, name, effects, source = row
            parts = []
            for e in json.loads(effects):
                r = self.fx_resource(e.get("resource"), active) if e.get("resource") else None
                parts.append({**e, "name": r[0] if r else None, "duration": r[1] if r else None,
                              "looping": r[2] if r else None, "file": r[3] if r else None})
            return {"kind": "MultiEffectInfo", "uuid": uuid, "name": name, "source": source, "effects": parts}
        r = self.fx_resource(guid, active)
        if r:
            return {"kind": "EffectResource", "uuid": guid, "name": r[0], "duration": r[1], "looping": r[2], "file": r[3], "source": r[4]}
        return None

    def effect_name(self, guid, active):
        e = self.effect(guid, active)
        return e["name"] if e else None

    def effect_users(self, guid, active, limit=30):
        w, p = self._where(active)
        rows = self.db.execute(f"SELECT DISTINCT name, type FROM stats WHERE {w} AND data LIKE ? ESCAPE '\\' LIMIT ?",
                               p + [like(guid), limit]).fetchall()
        return rows

    def search_effects(self, text, active, limit=30):
        """MultiEffectInfos whose own name or any component effect name contains `text`."""
        w, p = self._where(active)
        # every word must appear, in any order ("necrotic beam" matches ..._Necrotic_..._Beam_...)
        words = [t for t in re.split(r"[\s_]+", text) if t]
        cond = " AND ".join("name LIKE ? ESCAPE '\\'" for _ in words) or "1"
        pats = [like(t) for t in words]
        hits = {}
        for uuid, name, source in self.db.execute(f"SELECT uuid, name, source FROM mei WHERE {w} AND {cond} LIMIT ?",
                                                  p + pats + [limit * 4]):
            hits.setdefault(uuid, (name, source, "name"))
        fx_ids = [r[0] for r in self.db.execute(f"SELECT id FROM fx WHERE {w} AND {cond} LIMIT 2000", p + pats)]
        for i in range(0, len(fx_ids), 400):
            chunk = fx_ids[i:i + 400]
            cond = " OR ".join("effects LIKE ?" for _ in chunk)
            for uuid, name, source in self.db.execute(f"SELECT uuid, name, source FROM mei WHERE {w} AND ({cond})",
                                                      p + [f"%{c}%" for c in chunk]):
                hits.setdefault(uuid, (name, source, "component"))
            if len(hits) >= limit * 4:
                break
        out = []
        for uuid, (name, source, why) in list(hits.items())[: limit]:
            users = self.effect_users(uuid, active, limit=5)
            out.append((uuid, name, source, why, users))
        return out
