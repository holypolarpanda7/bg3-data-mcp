"""Plain-text rendering shared by the MCP server and the CLI."""
import json
import time

from . import index, sources


def layers(store):
    lines = ["Layers (load order; base always included):"]
    for name, kind, path, order, newest, indexed, counts in store.layer_rows():
        c = json.loads(counts)
        lines.append(f"  {order}. {name} [{kind}] sources newest {sources.iso(newest)}, indexed {sources.iso(indexed)}")
        lines.append("     " + ", ".join(f"{k} {c.get(k, 0)}" for k in ("stats", "loca", "templates", "prog", "lists", "mei", "fx")))
        lines.append(f"     {path}")
    errs = getattr(index.refresh, "last_errors", {})
    for name, err in errs.items():
        lines.append(f"  !! {name}: last rebuild failed: {err}")
    from . import deploy
    lines += deploy.deployed_lines(store)
    return "\n".join(lines)


def effect(store, guid, active):
    e = store.effect(guid, active)
    if not e:
        return f"no MultiEffectInfo or effect resource with GUID {guid}"
    if e["kind"] == "EffectResource":
        out = [f"EffectResource {e['name']} ({guid}) [{e['source']}]", f"  duration {e['duration']}, looping {e['looping']}", f"  file {e['file']}"]
    else:
        out = [f"MultiEffectInfo {e['name']} ({guid}) [{e['source']}]"]
        for c in e["effects"]:
            bones = ", ".join(c["target_bones"] + [f"src:{b}" for b in c["source_bones"]])
            try:
                dur = f"  {float(c['duration']):.1f}s" if c.get("duration") else ""
            except (TypeError, ValueError):
                dur = f"  {c['duration']}s"
            label = c["name"] or f"{c.get('resource') or '?'} [resource not indexed]"
            out.append(f"  - {label}" + (f"  start={c['start']}" if c.get("start") else "") + (f"  bones={bones}" if bones else "")
                       + dur + ("  looping" if c.get("looping") == "True" else ""))
    users = store.effect_users(guid, active)
    if users:
        out.append("used by: " + ", ".join(f"{n} ({t})" for n, t in users))
    return "\n".join(out)


def entry(store, name, active, provenance=True):
    r = store.resolve(name, active)
    if not r:
        return f"'{name}' not found in layers {active}"
    dn = store.display_name(r["fields"], active)
    out = [f"{name} ({r['type']})" + (f' - "{dn}"' if dn else ""), "inheritance: " + "  ->  ".join(r["chain"])]
    width = max((len(k) for k in r["fields"]), default=0)
    for k, (v, src) in sorted(r["fields"].items()):
        out.append(f"  {k:<{width}} = {v}" + (f"    [{src}]" if provenance else ""))
    return "\n".join(out)


def diff(store, name, layer, active):
    before, after, changes = store.diff(name, layer, active)
    if not after:
        return f"'{name}' not defined up to layer {layer}"
    if not changes:
        return f"{layer} does not change {name}"
    out = [f"{layer} changes to {name}" + ("" if before else " (entry is NEW in this layer)") + ":"]
    for k, b, a in changes:
        out.append(f"  {k}:\n     before: {b}\n     after:  {a}")
    return "\n".join(out)


def template(store, key, active):
    t = store.template(key, active)
    if not t:
        return f"template '{key}' not found"
    out = ["chain: " + "  ->  ".join(t["chain"])]
    for k, (v, src) in sorted(t["fields"].items()):
        if k.startswith("Vocal") or k.startswith("_OriginalFileVersion"):
            continue
        dn = ""
        if k in ("DisplayName", "Description") and v:
            r = store.loca(v, active)
            dn = f'  "{r[0][:80]}"' if r else ""
        out.append(f"  {k} = {v}{dn}    [{src}]")
    return "\n".join(out)


def progression(store, key, active, level=None):
    nodes = store.progression(key, active, level)
    if not nodes:
        return f"no progression nodes for '{key}'"
    out = []
    for lvl, name, table, src, a in nodes:
        parts = [f"L{lvl:<2} {name} [{src}]"]
        for k in ("PassivesAdded", "PassivesRemoved", "Selectors", "Boosts", "_SubClasses"):
            if a.get(k):
                parts.append(f"{k}={a[k]}")
        out.append("  " + " | ".join(parts))
    return f"table {nodes[0][2]}:\n" + "\n".join(out)
