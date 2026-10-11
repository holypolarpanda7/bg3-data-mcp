"""Offline self-test for bg3data/vortex_order.py sort(): dependencies first, tiers, pins, cycles."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bg3data import vortex_order as vo  # noqa: E402

DATA = "Public/X/Stats/Generated/Data/a.txt"


def mod(i, name, idx, deps=(), files=(DATA,)):
    e = {"id": name + ".pak", "name": name, "enabled": True, "index": idx, "data": {"uuid": f"u{i}", "folder": name}}
    return e, {"files": list(files), "deps": [{"UUID": f"u{d}", "Name": f"m{d}"} for d in deps], "meta": {}}


def run(specs, pins=None):
    es, infos = zip(*specs)
    a = {"entries": list(es), "infos": {e["id"]: i for e, i in zip(es, infos)}, "by_uuid": {e["data"]["uuid"]: e for e in es},
         "pins": pins or {}, "problems": []}
    out, notes = vo.sort(a)
    return [e["name"] for e in out], notes


fails = 0


def check(name, got, want):
    global fails
    fails += got != want
    print(f"{'ok  ' if got == want else 'FAIL'} {name}" + ("" if got == want else f": got {got!r}, want {want!r}"))


check("dependency first", run([mod(1, "addon", 0, deps=[2]), mod(2, "base", 1)])[0], ["base", "addon"])
check("library tier first", run([mod(1, "content", 0), mod(2, "util", 1, files=["Mods/util/ScriptExtender/Config.json"])])[0], ["util", "content"])
check("patch last", run([mod(1, "x patch", 0), mod(2, "content", 1)])[0], ["content", "x patch"])
check("order kept", run([mod(1, "b", 0), mod(2, "a", 1)])[0], ["b", "a"])
check("last pin", run([mod(1, "b", 0), mod(2, "a", 1)], {"last": ["b"]})[0], ["a", "b"])
check("cycle reported", any("cycle" in n for n in run([mod(1, "a", 0, deps=[2]), mod(2, "b", 1, deps=[1])])[1]), True)
check("missing dep reported", any("not installed" in n for n in run([mod(1, "a", 0, deps=[9])])[1]), True)
sys.exit(1 if fails else 0)
