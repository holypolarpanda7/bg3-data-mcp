"""Offline self-test for bg3data/gate.py and bg3data/release.py (2026-10-06): build report verdicts, change keys, Version64."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bg3data import gate, release  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f": got {got!r}, want {want!r}"))


P = gate.build_passed
check("pass to top", P("L2: ok 7s, LEVEL CHECK: ALL PASS\nL3: ok\ndone in 9s", 3), True)
check("KNOWN fail counted in header", P("L2: ok 7s, LEVEL CHECK: 1 FAIL\n    KNOWN spell X\ndone in 9s", 2), True)
check("detail FAIL", P("L2: ok\n    FAIL resource X\ndone in 9s"), False)
check("level-up failed", P("L13: LEVEL-UP FAILED - wanted spells not selected\ndone in 9s"), False)
check("stopped below top", P("L2: ok\ndone in 9s", 20), False)
check("no done line", P("host is level 3; build x starts at level 2"), False)
check("final cases short", P("L20: ok\n    final cases: 1/3 passed\ndone in 9s", 20), False)
check("final cases all", P("L20: ok\n    final cases: 3/3 passed\ndone in 9s", 20), True)
check("WARN doesn't fail", P("L2: ok\n    WARN resource X max 0\ndone in 9s", 2), True)
check("tests line FAIL", P("L5: ok\n    tests: 2/3 passed\n      case-x: FAIL\ndone in 9s", 5), False)

e = gate._stats_entries('new entry "A"\ntype "PassiveData"\ndata "X"  "1"\n\nnew entry "B"\ntype "SpellData"\n// note\n')
check("stats entries", sorted(e), ["A", "B"])
check("stats whitespace-insensitive", gate._stats_entries('new entry "A"\ntype "PassiveData"\ndata "X" "1"')["A"], e["A"])
check("numbered variant reached", gate._reached({"Interrupt_X_3", "Other_2"}, {"Interrupt_X"}), {"Interrupt_X_3"})

lsx = '<save><region id="P"><node id="root"><children><node id="Progression"><attribute id="Name" type="LSString" value="Fighter"/>' \
      '<attribute id="TableUUID" type="guid" value="t1"/><attribute id="UUID" type="guid" value="u1"/></node></children></node></region></save>'
n = gate._lsx_nodes(lsx)
check("lsx node key", list(n), ["Progression:u1"])
check("lsx node ids", n["Progression:u1"][1], {"UUID": "u1", "TableUUID": "t1", "Name": "Fighter"})
check("lsx unparsable", gate._lsx_nodes("<save>"), None)

check("version64 4.12.18.3", release.version64("4.12.18.3"), 145804076590825475)
check("version64 v1.0", release.version64("v1.0"), 36028797018963968)
check("version_str", release.version_str(145804076590825475), "4.12.18.3")
meta = ('<save><node id="Dependencies"><children><node id="ModuleShortDesc"><attribute id="Version64" type="int64" value="5"/></node>'
        '</children></node><node id="ModuleInfo"><attribute id="Version64" type="int64" value="36028797018963968"/>'
        '<children><node id="PublishVersion"><attribute id="Version64" type="int64" value="7"/></node></children></node></save>')
with tempfile.NamedTemporaryFile("w", suffix=".lsx", delete=False) as fh:
    fh.write(meta)
release.stamp(fh.name, "0.9.1")
out = open(fh.name).read()
os.remove(fh.name)
check("stamp only ModuleInfo", (out.count('value="5"'), out.count('value="7"'), str(release.version64("0.9.1")) in out), (1, 1, True))
check("version stamp ignored by gate", gate._strip_version(out) == gate._strip_version(meta), True)
check("dependency change not ignored", gate._strip_version(meta.replace('value="5"', 'value="6"')) == gate._strip_version(meta), False)

print(f"\n{'all passed' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
