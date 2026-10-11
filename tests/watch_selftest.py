"""Offline self-test for bg3data/watch.py _defs(): removed vs re-added entity keys from a -U0 diff."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bg3data import watch  # noqa: E402

G1, G2 = "11111111-2222-3333-4444-555555555555", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
D = f'''diff --git a/Public/M/Stats/Generated/Data/Spell.txt b/Public/M/Stats/Generated/Data/Spell.txt
--- a/Public/M/Stats/Generated/Data/Spell.txt
+++ b/Public/M/Stats/Generated/Data/Spell.txt
@@ -1,2 +1,2 @@
-new entry "Gone_Spell"
-new entry "Renamed_Old"
+new entry "Renamed_New"
+new entry "Moved_Spell"
diff --git a/Mods/M/Progressions.lsx b/Mods/M/Progressions.lsx
--- a/Mods/M/Progressions.lsx
+++ b/Mods/M/Progressions.lsx
@@ -1 +1 @@
-<attribute id="UUID" type="guid" value="{G1}"/>
-<attribute id="UUID" type="guid" value="{G2}"/>
+<attribute id="UUID" type="guid" value="{G2}"/>
'''
rem, add = watch._defs(D)
fails = 0
for name, got, want in (("removed-not-readded", sorted(rem - add), sorted(["Gone_Spell", "Renamed_Old", G1])),
                        ("readded GUID not breaking", G2 in (rem - add), False)):
    ok = got == want
    fails += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f": got {got!r}, want {want!r}"))
sys.exit(1 if fails else 0)
