"""Plain-text rendering shared by the MCP server and the CLI."""
import json
import time

from . import sources


def layers(store):
    lines = ["Layers (load order; base always included):"]
    for name, kind, path, order, newest, indexed, counts in store.layer_rows():
        c = json.loads(counts)
        lines.append(f"  {order}. {name} [{kind}] sources newest {sources.iso(newest)}, indexed {sources.iso(indexed)}"
                     f" | stats {c['stats']}, loca {c['loca']}, templates {c['templates']}, progression nodes {c['prog']}, lists {c['lists']}")
        lines.append(f"     {path}")
    return "\n".join(lines)


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
