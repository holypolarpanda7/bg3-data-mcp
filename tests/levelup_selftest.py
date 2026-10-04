"""Self-test for the level-up driver (bg3data/gameui.py).

Offline (default): pure logic, plus levelup_auto driven against a simulated level-up screen (rows with red markers, icon
pickers, a feat list) - checks the decisions it makes, without the game. Live (`--live`): one real level-up of the running
game's host (grant the XP first with bg3_level_up), validated by the level rising by one.

  UV_PROJECT_ENVIRONMENT=~/.cache/bg3-data-mcp/venv uv run python tests/levelup_selftest.py [--live]
"""
import sys
import time

from bg3data import gameui

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


class Patch:
    """Swap module attributes for a test and put them back afterwards."""

    def __init__(self, **kw):
        self.kw, self.old = kw, {}

    def __enter__(self):
        for k, v in self.kw.items():
            mod, name = (time, "sleep") if k == "sleep" else (gameui, k)
            self.old[k] = getattr(mod, name)
            setattr(mod, name, v)
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(time if k == "sleep" else gameui, "sleep" if k == "sleep" else k, v)


# ------------------------------------------------------------------------------------------------ pure logic
def test_pure():
    check("marker clusters (ring + glyph) merge into one row each", gameui._merge_rows([169, 176, 186, 257, 264, 274]) == [177, 265])
    check("rows 44 px apart stay separate", gameui._merge_rows([177, 221]) == [177, 221])

    with Patch(_fast=lambda cmd, timeout=15: (True, "ok 1440 236,352")):
        check("marker y is scaled to reference pixels (2560x1440 window)", gameui.pending_rows() == [177, 264], gameui.pending_rows())
    with Patch(_fast=lambda cmd, timeout=15: (True, "ok 1080")):
        check("no markers -> empty list", gameui.pending_rows() == [])
    with Patch(_fast=lambda cmd, timeout=15: (False, "boom")):
        check("helper failure -> None", gameui.pending_rows() is None)
    sent = []
    with Patch(_fast=lambda cmd, timeout=15: (sent.append(cmd), (True, "ok 1080"))[1]):
        gameui.pending_rows()
    check("marker strip is sent as fractions", sent and sent[0].startswith("redrows 0.00521 0.06481"), sent)

    # regression (2026-10-03): an empty marker list read as "still pending" -> extra icon clicks
    with Patch(pending_rows=lambda: []):
        check("empty marker list = row cleared", gameui._row_pending(177) is False)
    with Patch(pending_rows=lambda: None):
        check("unreadable markers = still pending (never assume success)", gameui._row_pending(177) is True)
    # regression (2026-10-03): tolerance 25 matched the next row (22 px away)
    with Patch(pending_rows=lambda: [199]):
        check("a marker on the next row (22 px) is a different row", gameui._row_pending(221) is False)
    with Patch(pending_rows=lambda: [178]):
        check("a marker a few px off is the same row", gameui._row_pending(177) is True)

    with Patch(_fast=lambda cmd, timeout=15: (True, "ok 12")):
        check("picker probe: dark panel everywhere -> no picker", gameui._picker_kind() is None)

    def bands(lit):
        def fake(cmd, timeout=15):
            y = float(cmd.split()[2]) * gameui.REF_H
            kind = next(k for k, b in gameui.ICON_PROBES.items() if abs(b[1] - y) < 1)
            return True, "ok " + str(lit.get(kind, 15))
        return fake
    with Patch(_fast=bands({"ritual": 90, "grid": 30})):
        check("picker probe: ritual page (grid band only catches its edge) -> ritual", gameui._picker_kind() == "ritual")
    with Patch(_fast=bands({"grid": 120})):
        check("picker probe: spell grid -> grid", gameui._picker_kind() == "grid")
    probes = sorted((b[1], b[1] + b[3]) for b in gameui.ICON_PROBES.values())
    check("picker probe bands don't overlap", all(a[1] <= b[0] for a, b in zip(probes, probes[1:])), probes)


def test_stable_rows():
    seq = iter([[222], [177, 222], [177, 222]])
    with Patch(pending_rows=lambda: next(seq, [177, 222]), sleep=lambda s: None):
        check("checklist fading in: waits for two equal reads", gameui.stable_pending_rows() == [177, 222])
    calls = []
    with Patch(pending_rows=lambda: (calls.append(1), [])[1], sleep=lambda s: None):
        r = gameui.stable_pending_rows()
    check("a level with no choices returns [] after 3 reads, not the full timeout", r == [] and len(calls) == 3, len(calls))


def test_enter_only_when_dark():
    seq = iter([95, 92, 0, 0, 18, 23, 23, 180])
    last = [None]
    keys = []

    def lum():
        last[0] = next(seq, 180)
        return last[0]

    clock = [0.0]

    def fake_time():
        clock[0] += 0.3
        return clock[0]

    with Patch(_state=lambda tries=3: {"known": True, "sheet_open": True, "levelup_open": False},
               levelup_state=lambda: {"known": True, "sheet_open": True, "levelup_open": True},
               click_frac=lambda fx, fy, right=False, count=1: True, _lum=lum,
               send_key=lambda scan, hold_ms=120, focus=True: keys.append((scan, last[0])), sleep=lambda s: None):
        real_time, time.time = time.time, fake_time
        try:
            gameui.levelup_open()
        finally:
            time.time = real_time
    check("Enter is pressed during the dark intro", any(k == 0x1C for k, _ in keys), keys)
    check("every Enter was sent while the screen was dark (never on the sheet or the interface)",
          all(k == 0x1C and l < gameui.DARK for k, l in keys), keys)

    with Patch(_state=lambda tries=3: {"known": False, "sheet_open": False, "levelup_open": False},
               send_key=lambda *a, **k: keys.append("SENT"), click_frac=lambda *a, **k: keys.append("CLICK")):
        keys.clear()
        st = gameui.levelup_open()
    check("Script Extender silent: no sheet toggle, no click", not keys and st.get("known") is False, keys)


# ------------------------------------------------------------------------------------------------ simulated screen
class FakeScreen:
    """A level-up screen: rows {y: [kind, needed, picked]}; kind is grid/ritual/savant/feat/subclass. Clicks go through _rclick."""

    def __init__(self, rows, taken_feats=(), level=4, rises=True, open_ok=True, capped=(), classes=None, wrong_class=False):
        self.rows = {y: [k, n, 0] for y, (k, n) in rows.items()}
        self.taken, self.level, self.rises, self.open_ok = set(taken_feats), level, rises, open_ok
        self.page, self.feat, self.extras, self.clicks = None, None, 0, []
        self.capped, self.asi_points = set(capped), 0       # abilities already at 20: their "+" does nothing
        self.classes, self.chosen, self.wrong_class = dict(classes or {"Wizard": level}), None, wrong_class

    def pending(self):
        return sorted(y for y, (k, n, p) in self.rows.items() if p < n)

    def rclick(self, x, y):
        self.clicks.append((x, y))
        if (x, y) == gameui.ADD_CLASS_BUTTON:
            return True
        hit_class = next((n for n, pos in gameui.CLASS_TILES.items() if pos == (x, y)), None)
        if hit_class:
            self.chosen = hit_class
            return True
        if x == gameui.CHECKLIST_X:
            self.page = min(self.rows, key=lambda r: abs(r - y))
            return True
        if self.page is None:
            return True
        kind, need, picked = self.rows[self.page]
        if kind in gameui.ICON_ORIGINS:
            ox, oy = gameui.ICON_ORIGINS[kind]
            on_icon = (x - ox) % gameui.ICON_STEP == 0 and (y - oy) % gameui.ICON_ROW == 0
            if on_icon and picked < need:
                self.rows[self.page][2] += 1
        elif kind == "feat":
            hit = next((f for f in gameui.FEATS if f[1] == (x, y)), None)
            if hit:
                self.feat, self.extras = (hit if hit[0] not in self.taken else None), 0
            elif self.feat and self.feat[2] == "asi" and x == gameui.ASI_PLUS[0]:
                ab = gameui.ABILITIES[(y - gameui.ASI_PLUS[1]) // 44]
                if ab not in self.capped and self.asi_points < 2:
                    self.asi_points += 1
                self.last_asi = ab
            elif self.feat and self.feat[2] != "asi" and (x, y) in self.feat[2]:
                self.extras += 1
            if self.feat and (self.asi_points >= 2 if self.feat[2] == "asi" else self.extras >= len(self.feat[2])):
                self.rows[self.page][2] = 1
        return True

    def patch(self):
        return Patch(
            host_level=lambda: self.level,
            levelup_open=lambda *a, **k: {"known": True, "sheet_open": True, "levelup_open": self.open_ok},
            levelup_state=lambda: {"known": True, "levelup_open": True, "can_feat": self.page is not None and self.rows[self.page][0] == "feat"},
            _state=lambda tries=3: {"known": True, "levelup_open": True, "complete": not self.pending()},
            pending_rows=lambda: [y for y in self.pending()],
            _picker_kind=lambda: (self.rows[self.page][0] if self.page is not None and self.rows[self.page][0] in gameui.ICON_ORIGINS else None),
            _rclick=self.rclick,
            levelup_finish=lambda wait=30.0: (self.apply(), (True, "accepted"))[1],
            class_levels=lambda: dict(self.classes),
            _asi_order=lambda: ["Intelligence", "Constitution", "Strength", "Dexterity", "Wisdom", "Charisma"],
            sleep=lambda s: None)

    def apply(self):
        if not self.rises:
            return
        self.level += 1
        cls = "Wizard" if (self.chosen is None or self.wrong_class) else self.chosen
        self.classes[cls] = self.classes.get(cls, 0) + 1


def test_auto():
    fs = FakeScreen({177: ("grid", 2), 265: ("savant", 1)}, level=2)
    with fs.patch():
        r = gameui.levelup_auto()
    check("auto: spells + savant -> ok, level 2 -> 3", r["ok"] and r["level_after"] == 3, r)
    check("auto: picks exactly what each row needs (3 icon clicks + 2 row clicks)", len(fs.clicks) == 5, fs.clicks)

    fs = FakeScreen({177: ("grid", 1), 221: ("ritual", 1), 390: ("feat", 1)}, taken_feats={"Ability Improvement", "Actor"})
    with fs.patch():
        r = gameui.levelup_auto()
    check("auto: unavailable feats (ASI, a taken Actor) fall through to the next in the chain", r["ok"] and any("feat Alert" in l for l in r["log"]), r["log"])
    check("auto: the ritual row is handled as a ritual picker (1 click)", any("ritual picker, 1 icon" in l for l in r["log"]), r["log"])

    fs = FakeScreen({177: ("grid", 2), 300: ("subclass", 1)})
    with fs.patch():
        r = gameui.levelup_auto()
    feat_clicks = [c for c in fs.clicks if c in [f[1] for f in gameui.FEATS]]
    check("auto: an unknown page is reported, not clicked like a feat list", not r["ok"] and not feat_clicks and "not handled" in r.get("error", ""), r)

    fs = FakeScreen({177: ("grid", 2)}, open_ok=False)
    with fs.patch():
        r = gameui.levelup_auto()
    check("auto: no level-up ready -> clear error, nothing clicked", not r["ok"] and "did not open" in r["error"] and not fs.clicks, r)

    fs = FakeScreen({177: ("grid", 2)}, rises=False)
    with fs.patch():
        r = gameui.levelup_auto()
    check("auto: accepted but the level didn't rise -> not ok", not r["ok"] and "did not rise" in r["error"], r)

    fs = FakeScreen({})
    with fs.patch():
        r = gameui.levelup_auto()
    check("auto: a level with no choices goes straight to Accept", r["ok"] and not fs.clicks, r)

    fs = FakeScreen({390: ("feat", 1)}, taken_feats={"Actor"}, level=7)
    with fs.patch():
        r = gameui.levelup_auto()
    check("auto: a feat level takes Ability Improvement, +2 to the primary ability",
          r["ok"] and any("Ability Improvement (Intelligence)" in l for l in r["log"]), r["log"])
    fs = FakeScreen({390: ("feat", 1)}, capped={"Intelligence"}, level=7)
    with fs.patch():
        r = gameui.levelup_auto()
    check("auto: a capped primary ability spills over to Constitution",
          r["ok"] and any("(Constitution)" in l for l in r["log"]), r["log"])

    fs = FakeScreen({}, level=8, classes={"Wizard": 8})
    with fs.patch():
        r = gameui.levelup_auto(add_class="Cleric")
    check("auto: add_class takes the level in that class and validates it",
          r["ok"] and r["classes_after"] == {"Wizard": 8, "Cleric": 1} and (gameui.CLASS_TILES["Cleric"] in fs.clicks), r)
    fs = FakeScreen({}, level=8, classes={"Wizard": 8}, wrong_class=True)
    with fs.patch():
        r = gameui.levelup_auto(add_class="Cleric")
    check("auto: the level landing in the wrong class fails validation", not r["ok"] and "another class" in r.get("error", ""), r)
    fs = FakeScreen({}, level=8)
    with fs.patch():
        r = gameui.levelup_auto(add_class="Artificer")
    check("auto: an unknown class name is refused before clicking a tile",
          not r["ok"] and "unknown class" in r["error"] and not fs.clicks, r)

    with Patch(host_level=lambda: None):
        r = gameui.levelup_auto()
    check("auto: no host level -> error before anything is clicked", not r["ok"] and "no host level" in r["error"], r)


def test_helper_restart():
    import os
    d = gameui._InputDaemon()
    d.mtime = os.path.getmtime(d.SCRIPT)
    check("helper is fresh while inputd.ps1 is unchanged", d._stale() is False)
    d.mtime -= 10
    check("helper is stale (restarts) after inputd.ps1 changed", d._stale() is True)


def live():
    lv = gameui.host_level()
    t = time.time()
    r = gameui.levelup_auto()
    ok = bool(r.get("ok")) and r.get("level_after") == (lv or 0) + 1
    # a row that only cleared after trying many icons means the picker was misread (it worked by luck)
    lucky = [l for l in r.get("log", []) if "picker," in l and int(l.split("picker, ")[1].split()[0]) > 2]
    check("live: every picker cleared within 2 clicks (picker type read right)", not lucky, lucky)
    check(f"live: auto level {lv} -> {r.get('level_after')} in {time.time() - t:.1f}s "
          f"(open {r.get('open_s')}s, choices {r.get('choices_s')}s)", ok, r.get("error", r.get("log")))
    print("   log:", r.get("log"))


if __name__ == "__main__":
    test_pure()
    test_stable_rows()
    test_enter_only_when_dark()
    test_auto()
    test_helper_restart()
    if "--live" in sys.argv:
        live()
    print(f"{len(FAILS)} FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
    sys.exit(1 if FAILS else 0)
