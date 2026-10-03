"""Which stats icons the game really has. The running game's client lists every icon it can draw
(Ext.StaticData.GetTextureAtlasManager().IconMap, 5395 names on 2026-10-02); bg3_icon_check caches that set in
<cache>/icons.json, reports a layer's icons that aren't in it (they show blank) with the closest real names, and
bg3_lint_stats uses the cache offline."""
import difflib
import json
import os
import re

from . import platform, se

CACHE_FILE = os.path.join(platform.cache_dir(), "icons.json")


def load():
    """Set of icon names the game has (empty if bg3_icon_check never ran)."""
    try:
        return set(json.load(open(CACHE_FILE)).get("icons", []))
    except (OSError, ValueError, AttributeError):
        return set()


def fetch():
    names = []
    for start in range(0, 20000, 1500):
        r = se.eval_lua("local out,i={},0 for k,_ in pairs(Ext.StaticData.GetTextureAtlasManager().IconMap) do i=i+1 "
                        f"if i>{start} and i<={start + 1500} then out[#out+1]=tostring(k) end end return out", "client", timeout=60)
        if not r["ok"]:
            raise RuntimeError(f"the game didn't answer (is it running?): {r['result']}")
        chunk = r["result"] or []
        names += chunk
        if len(chunk) < 1500:
            break
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    json.dump({"icons": sorted(set(names))}, open(CACHE_FILE, "w"))
    return set(names)


def suggest(name, known, n=3):
    stem = re.sub(r"^(PassiveFeature|Action|Spell_[A-Za-z]+|Status|Skill)_", "", name)
    words = [w for w in re.findall(r"[A-Z][a-z]+|[A-Z]+(?![a-z])", stem) if len(w) > 2]
    hits = [k for k in known if stem.lower() in k.lower()]
    if not hits and words:
        hits = [k for k in known if sum(w.lower() in k.lower() for w in words) >= max(1, len(words) - 1)]
    pool = hits or list(known)
    return difflib.get_close_matches(name, pool, n=n, cutoff=0.0) if pool else []


def check(store, layer, active):
    known = fetch()
    rows = store.db.execute("SELECT name, file, data FROM stats WHERE layer=?", (layer,)).fetchall()
    uses = {}
    for name, file, data in rows:
        icon = json.loads(data).get("Icon")
        if icon:
            uses.setdefault(icon, []).append((name, os.path.basename(file or "")))
    missing = sorted(n for n in uses if n not in known)
    out = [f"{layer}: {len(uses)} icons used, {len(missing)} missing in game ({len(known)} icons cached in {CACHE_FILE})"]
    for n in missing:
        ents = uses[n]
        out.append(f"  {n} -> try {', '.join(suggest(n, known)) or '?'}: " + ", ".join(f"{e} [{f}]" for e, f in ents[:3])
                   + (f" +{len(ents) - 3}" if len(ents) > 3 else ""))
    return "\n".join(out)
