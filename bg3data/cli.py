"""CLI over the same queries, for testing and shell use.

  bg3-data layers | refresh [layer|all] | entry NAME [--layers a,b] | diff NAME LAYER |
           search TEXT [--type T] [--field F] | refs TOKEN | loca Q | template KEY |
           progression KEY [--level N] | visuals NAME | similar [--damage D] [--school S] [--keyword K] |
           convert IN OUT  (LSX <-> LSF/LSJ via Divine; formats from the extensions)
"""
import argparse
import sys

from . import format as fmt
from . import index, query, sources


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bg3-data")
    ap.add_argument("cmd")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--layers")
    ap.add_argument("--type")
    ap.add_argument("--field")
    ap.add_argument("--level", type=int)
    ap.add_argument("--damage")
    ap.add_argument("--school")
    ap.add_argument("--keyword")
    ap.add_argument("--spell-type")
    ap.add_argument("--limit", type=int, default=25)
    a = ap.parse_args(argv)
    log = lambda m: print(m, file=sys.stderr)
    if a.cmd == "convert":
        from . import platform
        src, dst = a.args[0], a.args[1]
        ext = lambda f: f.rsplit(".", 1)[-1].lower()
        print(sources.divine(sources.load_config(), "-a", "convert-resource", "-s", platform.to_win(src),
                             "-d", platform.to_win(dst), "-i", ext(src), "-o", ext(dst)).strip())
        return
    if a.cmd == "refresh":
        force = None if not a.args else (a.args[0] if a.args[0] == "all" else [a.args[0]])
        index.refresh(force=force, log=log)
        a.cmd = "layers"
    s = query.Store(refresh=a.cmd != "layers", log=log)
    active = s.active(a.layers.split(",") if a.layers else None)
    if a.cmd == "layers":
        print(fmt.layers(s))
    elif a.cmd.startswith("deps-"):  # deps-status|deps-diff|deps-lock|deps-update LAYER [DEP|apply]
        from . import deps
        sub, layer = a.cmd[5:], a.args[0]
        print({"status": lambda: deps.status(s, layer),
               "diff": lambda: deps.diff(s, layer, a.args[1] if len(a.args) > 1 else None),
               "lock": lambda: deps.lock_deps(s, layer),
               "update": lambda: deps.update(s, layer, apply="apply" in a.args[1:], log=log)}[sub]())
    elif a.cmd == "entry":
        print(fmt.entry(s, a.args[0], active))
    elif a.cmd == "diff":
        print(fmt.diff(s, a.args[0], a.args[1], active))
    elif a.cmd == "search":
        for n, t, l in s.search_stats(a.args[0], active, a.type, a.field, a.limit):
            print(f"{n} ({t}) [{l}]")
    elif a.cmd == "refs":
        for t, n, l, src in s.references(a.args[0], active, a.limit):
            print(f"{t:9s} {n} [{src}]")
    elif a.cmd == "loca":
        q = a.args[0]
        r = s.loca(q, active) if q.startswith("h") and " " not in q else None
        if r:
            print(r)
        else:
            for h, t, src in s.loca_search(q, active, a.limit):
                print(h, t[:150], src)
    elif a.cmd == "template":
        print(fmt.template(s, a.args[0], active))
    elif a.cmd == "progression":
        print(fmt.progression(s, a.args[0], active, a.level))
    elif a.cmd == "visuals":
        r = s.resolve(a.args[0], active)
        for k, v in sorted(s.visual_kit(r["fields"]).items()) if r else []:
            print(f"{k} = {v}")
    elif a.cmd == "similar":
        for n, dn, lvl, sch, kit in s.similar_spells(active, a.damage, a.school, a.spell_type, a.level, a.keyword, a.limit):
            print(f"{n} \"{dn}\" L{lvl} {sch}: " + ", ".join(f"{k}" for k in kit if "Effect" in k))
    elif a.cmd == "effect":
        print(fmt.effect(s, a.args[0].lower(), active))
    elif a.cmd == "fxsearch":
        for uuid, name, src, why, users in s.search_effects(a.args[0], active, a.limit):
            print(f"{uuid} {name} [{src}, {why}]" + (" used by " + ", ".join(n for n, _ in users) if users else ""))
    else:
        ap.error(f"unknown command {a.cmd}")


if __name__ == "__main__":
    main()
