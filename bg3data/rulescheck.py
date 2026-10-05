"""Level-by-level check of a mod's class and subclass progressions against the RULES (2026-10-05), not against the mod's own
data: what each level should grant comes from the layer's suite folder -

  <suite>/rules/classes.toml           [[class_level]] class, level, features = [...], cantrips, prepared, slots_<n>, ...
  <suite>/rules/local/subclasses.toml  [[subclass_feature]] class, subclass, level, name  (book-derived, kept local)
  <suite>/rules/aliases.toml           optional: [feature] "Rules name" = ["PassiveName", ...] or "skip";
                                       [subclass] "ModSubclassName" = "Rules subclass name"; [technical] names = [...]

What it reports, for every class and subclass level in range:
  MISSING  a rules feature the mod doesn't grant at that level (matched by display name, or an alias)
  EXTRA    a feature the mod grants at that level that the rules don't list there (moved, homebrew, or a leftover)
  CHOICE   a choice the level should offer and doesn't, or with the wrong count: feat (Ability Score Improvement), Epic
           Boon, Metamagic, invocations, cantrips, Mystic Arcanum
  SLOTS    spell slots granted at the level differ from the table's increase
  SOURCE   a mod subclass with no rules source to check it against
"""
import glob
import json
import os
import re
import tomllib

from . import testing

TECHNICAL = re.compile(r"^(UnlockedSpellSlotLevel\d|.*_Unlock$|.*Technical.*|.*_Hidden$)")
EMPTY = {"passives": [], "spells": [], "resources": [], "selectors": [], "feat": False, "slots": {}, "ours": False, "removed": []}
GENERIC = {"subclass feature", "ability score improvement", "epic boon", "metamagic", "eldritch invocations"}


def _norm(s):
    s = re.sub(r"\([^)]*\)", "", str(s or "")).lower().replace("’", "'")
    return re.sub(r"[^a-z0-9]+", "", s)


def _matches(rule_name, display):
    a, b = _norm(rule_name), _norm(display)
    if not a or not b:
        return False
    return a == b or (len(a) >= 5 and a in b) or (len(b) >= 5 and b in a)


def load_rules(layer):
    classes, subs, aliases = {}, {}, {"feature": {}, "subclass": {}, "technical": {"names": []}}
    conf, nosource = {}, {}
    for d in testing.suite_dirs(layer):
        for f in glob.glob(os.path.join(d, "rules", "*.toml")) + glob.glob(os.path.join(d, "rules", "local", "*.toml")):
            with open(f, "rb") as fh:
                doc = tomllib.load(fh)
            for r in doc.get("class_level", []):
                classes.setdefault(r["class"], {})[r["level"]] = r
            for r in doc.get("subclass_feature", []):
                lv = subs.setdefault((r["class"], r["subclass"]), {})
                if r.get("level") and r.get("name"):
                    lv.setdefault(r["level"], []).append(r["name"])
                    conf[(r["class"], r["subclass"], r["name"])] = r.get("confidence", "")
                elif r.get("confidence") == "none":
                    nosource[(r["class"], r["subclass"])] = r.get("source", "")
            for k in ("feature", "subclass", "technical"):
                aliases[k].update(doc.get(k, {}) if os.path.basename(f) in ("aliases.toml", "feature_notes.toml") else {})
    return classes, subs, aliases, conf, nosource


_FILLER = re.compile(r"^(pathofthe|pathof|collegeofthe|collegeof|circleofthe|circleof|oathofthe|oathof|wayofthe|wayof|warriorofthe|"
                     r"warriorof|the)|(domain|school|patron|sorcery|path|college|circle|guild)$")


def _sub_norm(s):
    n = _norm(s)
    for _ in range(2):
        n = _FILLER.sub("", n)
    return n


def _granted(store, active, nodes, layer):
    """Per level: what the nodes grant - passives [(name, display)], selectors [(kind, args)], feat, slot boosts, ours?"""
    out = {}
    for lvl, _name, _t, src, a in nodes:
        if str(a.get("IsMulticlass", "")).lower() == "true":
            continue
        g = out.setdefault(lvl, {"passives": [], "spells": [], "resources": [], "selectors": [], "feat": False, "slots": {},
                                 "ours": False, "removed": []})
        g["ours"] |= src == layer or src.startswith(layer)
        for p in filter(None, (a.get("PassivesAdded") or "").split(";")):
            r = store.resolve(p, active)
            g["passives"].append((p, store.display_name((r or {}).get("fields", {}), active) if r else ""))
        g["removed"] += [p for p in (a.get("PassivesRemoved") or "").split(";") if p]
        for kind, args in re.findall(r"(\w+)\(([^)]*)\)", a.get("Selectors") or ""):
            g["selectors"].append((kind, [x.strip() for x in args.split(",")]))
            if kind == "AddSpells":
                for sp in testing._list_spells(store, active, args.split(",")[0].strip()) or []:
                    r = store.resolve(sp, active)
                    g["spells"].append((sp, store.display_name((r or {}).get("fields", {}), active) if r else ""))
        if str(a.get("AllowImprovement", "")).lower() == "true":
            g["feat"] = True
        for rname, rlvl, amt in testing._boost_resources(a.get("Boosts")):
            if rname == "SpellSlot":
                g["slots"][rlvl] = g["slots"].get(rlvl, 0) + amt
            else:
                g["resources"].append(rname)
        for b in re.findall(r"UnlockSpell\((\w+)", a.get("Boosts") or ""):
            r = store.resolve(b, active)
            g["spells"].append((b, store.display_name((r or {}).get("fields", {}), active) if r else ""))
    return out


def _sel_count(g, test):
    return sum(int(float(a[1] or 0)) if len(a) > 1 else 1 for k, a in g["selectors"] if test(k, a))


def _check_features(where, L, want, g, aliases, technical, out):
    """want: rules feature names at L; g: granted. Appends MISSING / EXTRA lines; returns the matched passive names."""
    used = set()
    for fname in want:
        if _norm(fname) in {_norm(x) for x in GENERIC}:
            continue
        al = aliases["feature"].get(fname)
        if al == "skip":
            continue
        pool = g["passives"] + g["spells"]
        hit = [p for p, d in pool if (al and p in al) or _matches(fname, d) or _matches(fname, p)]
        # a feature that only adds uses ("Indomitable (two uses)", "Action Surge (two uses)") is a resource boost
        if not hit and re.search(r"\((?:\w+ )?uses?\)|\(\w+ uses\)", fname):
            hit = [r for r in g["resources"] if _matches(fname, r)]
        if hit:
            used.update(hit)
        else:
            out.append(f"  MISSING {where} L{L}: {fname}")
    for p, d in g["passives"]:
        if p in used or TECHNICAL.match(p) or p in technical or not d:
            continue
        if any(p in (v if isinstance(v, list) else []) for v in aliases["feature"].values()):
            continue
        out.append(f"  EXTRA   {where} L{L}: {p} ({d}) - not in the rules at this level")
    return used


def lint_rules(store, active, layer, lo=13, hi=20):
    classes, subs, aliases, conf, nosource = load_rules(layer)
    if not classes:
        return f"no rules tables for {layer} (expected <suite>/rules/classes.toml - Scripts/gen_rules_tables.py)"
    technical = set(aliases["technical"].get("names", []))
    w, p = store._where(active)
    cds = {}
    for u, n, at in store.db.execute(f"SELECT uuid, name, attrs FROM staticdata WHERE kind='ClassDescription' AND {w} ORDER BY rank", p):
        cds[u] = (n, json.loads(at))
    out, summary = [], []
    for cu, (cname, ca) in sorted(cds.items(), key=lambda t: t[1][0]):
        if ca.get("ParentGuid") or cname not in classes or not ca.get("ProgressionTableUUID"):
            continue
        rows = classes[cname]
        got = _granted(store, active, store.progression(ca["ProgressionTableUUID"], active), layer)
        n0 = len(out)
        for L in range(lo, hi + 1):
            row, prev = rows.get(L), rows.get(L - 1, {})
            g = got.get(L, EMPTY)
            if not row:
                continue
            feats = row.get("features", [])
            _check_features(cname, L, feats, g, aliases, technical, out)
            fn = {_norm(f) for f in feats}
            if (_norm("Ability Score Improvement") in fn) != g["feat"]:
                out.append(f"  CHOICE  {cname} L{L}: feat/Ability Score Improvement {'missing' if not g['feat'] else 'offered, the rules have none here'}")
            eb = _sel_count(g, lambda k, a: k == "SelectPassives" and len(a) > 2 and a[2] == "EpicBoon")
            if (_norm("Epic Boon") in fn) != (eb > 0):
                out.append(f"  CHOICE  {cname} L{L}: Epic Boon {'missing' if not eb else 'offered, the rules have none here'}")
            if cname == "Sorcerer":
                mm = _sel_count(g, lambda k, a: k == "SelectPassives" and len(a) > 2 and a[2] == "Metamagic")
                if (_norm("Metamagic") in fn) != (mm > 0):
                    out.append(f"  CHOICE  {cname} L{L}: Metamagic {'missing' if not mm else 'offered, the rules have none here'}")
            if "invocations" in row:
                inv = _sel_count(g, lambda k, a: k == "SelectPassives" and len(a) > 2 and "Invocation" in a[2])
                want = row["invocations"] - prev.get("invocations", 0)
                if inv != want:
                    out.append(f"  CHOICE  {cname} L{L}: {inv} invocation pick(s), the table adds {want}")
            if "cantrips" in row:
                ct = _sel_count(g, lambda k, a: k == "SelectSpells" and any("Cantrip" in x for x in a[3:]))
                want = row["cantrips"] - prev.get("cantrips", 0)
                if ct != want:
                    out.append(f"  CHOICE  {cname} L{L}: {ct} cantrip pick(s), the table adds {want}")
            for f in feats:
                m = re.match(r"Mystic Arcanum \(level (\d) spell\)", f)
                if m and not _sel_count(g, lambda k, a: k == "SelectSpells" and len(a) > 5 and a[5] == "None"):
                    out.append(f"  CHOICE  {cname} L{L}: Mystic Arcanum (level {m.group(1)}) - no slot-free spell choice")
            for k in range(1, 10):
                col = f"slots_{k}"
                if col in row:
                    want = row[col] - prev.get(col, 0)
                    have = g["slots"].get(k, 0)
                    if have != want:
                        out.append(f"  SLOTS   {cname} L{L}: +{have:g} level-{k} slot(s), the table adds {want}")
            # subclasses: the class table says "Subclass feature" where the subclasses get theirs
            if _norm("Subclass feature") in fn:
                pass
        # subclass levels
        for su, (sname, sa) in sorted(cds.items(), key=lambda t: t[1][0]):
            if sa.get("ParentGuid") != cu or not sa.get("ProgressionTableUUID"):
                continue
            disp = store.loca(sa.get("DisplayName", ""), active) if sa.get("DisplayName") else None
            disp = disp[0] if disp else sname
            key = aliases["subclass"].get(sname)
            # exact names only (after dropping "Path of the", "Domain"...): a substring once matched Twilight to Light Domain
            cand = [s for (c, s) in subs if c == cname and (s == key or (not key and _sub_norm(s) in (_sub_norm(disp), _sub_norm(sname))))]
            sgot = _granted(store, active, store.progression(sa["ProgressionTableUUID"], active), layer)
            ours = [L for L in range(lo, hi + 1) if sgot.get(L, {}).get("ours")]
            if not cand:
                if ours:
                    out.append(f"  SOURCE  {cname}/{sname} ({disp}): no rules source for this subclass - its 13-20 features are unchecked")
                continue
            if (cname, cand[0]) in nosource:
                for L in ours:
                    for p_, d_ in sgot[L]["passives"]:
                        if not TECHNICAL.match(p_) and d_:
                            out.append(f"  NOSRC   {cname}/{sname} L{L}: {p_} ({d_}) - the subclass has no source past 12 "
                                       f"({nosource[(cname, cand[0])]}): this is the mod's own design")
                continue
            sfeat = subs[(cname, cand[0])]
            for L in range(lo, hi + 1):
                g = sgot.get(L, EMPTY)
                n_before = len(out)
                _check_features(f"{cname}/{sname}", L, sfeat.get(L, []), g, aliases, technical, out)
                for k in range(n_before, len(out)):   # say how sure the expectation is
                    m = re.match(r"  MISSING \S+ L\d+: (.+)$", out[k])
                    c = conf.get((cname, cand[0], m.group(1))) if m else None
                    if c and c != "verified":
                        out[k] += f"   [{c}]"
        summary.append(f"{cname}: {len(out) - n0}")
    head = f"rules check {layer} L{lo}-{hi}: {len(out)} finding(s) (" + ", ".join(summary) + ")"
    return head + ("\n" + "\n".join(out) if out else " - clean")
