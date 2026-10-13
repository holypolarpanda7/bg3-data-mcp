"""Self-test of bg3data/safety.py against fake save folders (no game, nothing real is touched): python tests/safety_selftest.py"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bg3data import safety  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


tmp = tempfile.mkdtemp(prefix="safety_selftest_")
try:
    sg, vault = os.path.join(tmp, "Story"), os.path.join(tmp, "vault")
    honour = os.path.join(sg, "6d5e3180-aaaa__HonourMode")
    other = os.path.join(sg, "mimir-5991262935__NewGame Probe Wizard")
    harness = os.path.join(sg, "Tavizard-21111268955__wiz-evoker L20")
    write(os.path.join(honour, "HonourMode.lsv"), b"honour-save-data" * 1000)
    write(os.path.join(honour, "HonourMode.WebP"), b"img")
    write(os.path.join(other, "probe.lsv"), b"probe")
    write(os.path.join(harness, "wiz.lsv"), b"harness")
    quiet = lambda *a: None  # noqa: E731

    names = [n for n, _ in safety.protected_folders(sg, "Tavizard")]
    check("harness saves are not protected, everything else is", names == sorted([os.path.basename(honour), os.path.basename(other)]), str(names))

    v1 = safety.protect(sg, "Tavizard", vault, quiet)
    check("every protected folder is vaulted", set(v1) == set(names))
    copied = os.path.join(v1[os.path.basename(honour)], "save", "HonourMode.lsv")
    check("the vaulted copy has the same bytes", os.path.exists(copied) and open(copied, "rb").read() == open(os.path.join(honour, "HonourMode.lsv"), "rb").read())
    check("the harness save is not vaulted", not os.path.exists(os.path.join(vault, os.path.basename(harness))))
    v2 = safety.protect(sg, "Tavizard", vault, quiet)
    check("a second protect() of an unchanged state adds no version", v1 == v2 and len(os.listdir(os.path.join(vault, os.path.basename(honour)))) == 1)

    # versions: a change adds one, retention keeps KEEP_VERSIONS
    for i in range(5):
        write(os.path.join(honour, "HonourMode.lsv"), f"changed-{i}".encode())
        time.sleep(0.02)
        safety.protect(sg, "Tavizard", vault, quiet)
    check(f"retention keeps the last {safety.KEEP_VERSIONS} versions", len(os.listdir(os.path.join(vault, os.path.basename(honour)))) == safety.KEEP_VERSIONS)

    # baseline + violations
    n = safety.begin_session(sg, "Tavizard", vault, quiet)
    check("begin_session baselines the protected files", n == 3 and safety.session_violations() == [])
    write(os.path.join(harness, "wiz.lsv"), b"harness changed")
    write(os.path.join(sg, "Tavizard-99__new", "x.lsv"), b"new harness save")
    check("changing or adding HARNESS saves is no violation", safety.session_violations() == [])
    write(os.path.join(honour, "HonourMode.lsv"), b"modified by someone")
    check("a changed protected file is a violation", any(v.startswith("changed:") and "HonourMode.lsv" in v for v in safety.session_violations()))
    time.sleep(0.02)
    write(os.path.join(honour, "HonourMode.lsv"), b"modified by someone")   # identical bytes rewritten: only the mtime differs
    check("a rewrite with identical bytes (new mtime) is still reported", any("HonourMode.lsv" in v for v in safety.session_violations()))
    safety.begin_session(sg, "Tavizard", vault, quiet)
    write(os.path.join(other, "extra.lsv"), b"new")
    check("a new file in a protected folder is a violation", any(v.startswith("new:") for v in safety.session_violations()))
    safety.begin_session(sg, "Tavizard", vault, quiet)
    os.remove(os.path.join(other, "probe.lsv"))
    check("a missing protected file is a violation", any(v.startswith("missing:") for v in safety.session_violations()))

    # watchdog: identity trigger, violation trigger, no false alarm
    killed = []
    safety.begin_session(sg, "Tavizard", vault, quiet)
    w = safety.Watchdog(interval=0.05, kill=lambda: killed.append(1), identity=lambda: "Gregor", deny=["Gregor"], vault=vault, is_up=lambda: True)
    w.start(); w.join(2)
    check("the watchdog kills the game when the host is a protected leader", killed == [1] and "Gregor" in (w.alert or ""))
    killed.clear()
    w = safety.Watchdog(interval=0.05, kill=lambda: killed.append(1), identity=lambda: "Tavizard", deny=["Gregor"], vault=vault, is_up=lambda: True)
    w.start(); time.sleep(0.5); w.stop(); w.join(1)
    check("the watchdog leaves a harness host alone", killed == [] and w.checked >= 3)
    killed.clear()
    w = safety.Watchdog(interval=0.05, kill=lambda: killed.append(1), identity=lambda: None, deny=["Gregor"], vault=vault, is_up=lambda: True)
    write(os.path.join(honour, "HonourMode.lsv"), b"tampered")
    w.start(); w.join(2)
    check("the watchdog kills the game when a protected file changes", killed == [1] and "changed" in (w.alert or ""))
    # session tie: a watchdog must not outlive its game (it would see the user's own later game)
    safety.begin_session(sg, "Tavizard", vault, quiet)      # a fresh baseline: the test above tampered with the file
    killed.clear(); up = [True]
    w = safety.Watchdog(interval=0.05, kill=lambda: killed.append(1), identity=lambda: "Gregor", deny=["Gregor"], vault=vault, is_up=lambda: up[0])
    up[0] = False                      # never came up -> ends after the grace period without killing anything
    w.grace = 0.2
    w.start(); w.join(2)
    check("a watchdog whose game never came up ends without acting", killed == [] and w.ended == "the game never came up" and not w.is_alive())
    killed.clear(); up = [True]
    w = safety.Watchdog(interval=0.05, kill=lambda: killed.append(1), identity=lambda: "Tavizard", deny=["Gregor"], vault=vault, is_up=lambda: up[0])
    w.start(); time.sleep(0.3); up[0] = False; w.join(2)
    check("a watchdog ends when its game exits", w.ended == "the game exited" and not w.is_alive())
    write(os.path.join(honour, "HonourMode.lsv"), b"the user plays their own game afterwards")
    time.sleep(0.4)
    check("...and never fires on what happens after (the user's own session)", killed == [])
    check("alerts are logged", os.path.exists(os.path.join(vault, "alerts.log")) and "KILLED" in open(os.path.join(vault, "alerts.log")).read())

    # Mod Verification box gate
    foreign = ["Mod Verification", "We have detected changes to the following mods since your last playthrough.", "Unresolved mod issues.",
               "Missing or Disabled", "CommunityLibrary", "Subscribe", "CompatibilityFramework"]
    mine = ["Mod Verification", "We have detected changes...", "Version Mismatch", "DnD 5.5e All-in-One BEYOND", "Downgrade",
            "New Mods Detected", "ImpUI (ImprovedUI)", "VFX_Library_VladsCodex"]
    check("a box with a Missing or Disabled section is foreign (never Start Game)", safety.classify_mod_verification(foreign)[0] == "foreign")
    check("a box with only version mismatch / new mods is a harness save's", safety.classify_mod_verification(mine)[0] == "harness")
    check("a box with no recognisable sections is unknown (never Start Game)", safety.classify_mod_verification(["Mod Verification", "OK"])[0] == "unknown")
    check("an empty read is unknown", safety.classify_mod_verification([])[0] == "unknown")
    # the box's view model rows (gameui.read_mod_verification): "section:<ReflectionString>" + mod names
    check("view model: only the Extra section (new mods) is a harness save's", safety.classify_mod_verification(["section:Extra", "ImpUI (ImprovedUI)", "SCO"])[0] == "harness")
    check("view model: a Missing section is foreign", safety.classify_mod_verification(["section:Extra", "a", "section:Missing", "b"])[0] == "foreign")
    check("view model: an unrecognised section is unknown (never Start Game)", safety.classify_mod_verification(["section:Extra", "a", "section:Updated", "b"])[0] == "unknown")
    st = safety.status(vault, sg, "Tavizard")
    check("status reports protected folders, the baseline and the restore hint", "protected save folders: 2" in st and "restore" in st.lower())
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    safety._state.clear()

print("\nall passed" if not fails else f"\n{len(fails)} FAILED: {', '.join(fails)}")
sys.exit(1 if fails else 0)
