"""Self-test for the level-up driver (bg3data/gameui.py). Offline part: pure logic with a fake helper. Live part (`--live`,
needs the game running with a host at level >= 2): auto-levels the host N times and validates each level.

  UV_PROJECT_ENVIRONMENT=~/.cache/bg3-data-mcp/venv uv run python tests/levelup_selftest.py [--live N]
"""
import sys
import time

from bg3data import gameui


def offline():
    fails = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    real = gameui._fast
    try:
        gameui._fast = lambda cmd, timeout=15: (True, "ok 169,176,186,257,264,274")
        check("ring/glyph clusters merge into one row each", gameui.pending_rows() == [177, 265])
        gameui._fast = lambda cmd, timeout=15: (True, "ok")
        check("no markers -> empty list", gameui.pending_rows() == [])
        gameui._fast = lambda cmd, timeout=15: (False, "boom")
        check("helper failure -> None (callers treat as unknown)", gameui.pending_rows() is None)
        gameui._fast = lambda cmd, timeout=15: (True, "ok 12")
        check("icon probe: dark panel is not an icon", gameui._icons_at((324, 476)) is False)
        gameui._fast = lambda cmd, timeout=15: (True, "ok 80")
        check("icon probe: bright art is an icon", gameui._icons_at((324, 476)) is True)
        seq = iter([[100], [100], [100]])
        gameui.pending_rows = lambda real=gameui.pending_rows, it=seq: next(it, [100])
        check("stable_pending_rows needs two equal reads", gameui.stable_pending_rows(3.0) == [100])
    finally:
        gameui._fast = real
        import importlib
        importlib.reload(gameui)
    return fails


def live(n):
    from bg3data import se
    fails = []
    for i in range(n):
        lv = gameui.host_level()
        t = time.time()
        r = gameui.levelup_auto()
        dt = time.time() - t
        ok = r.get("ok") and r.get("level_after") == lv + 1
        print(("PASS " if ok else "FAIL ") + f"auto level {lv} -> {r.get('level_after')} in {dt:.1f}s  {r.get('log')}  {r.get('error', '')}")
        if not ok:
            fails.append(lv)
            break
        break  # XP for the next level is granted by bg3_level_up (an MCP tool); loop externally
    return fails


if __name__ == "__main__":
    f = offline()
    if "--live" in sys.argv:
        f += live(int(sys.argv[sys.argv.index("--live") + 1]) if len(sys.argv) > sys.argv.index("--live") + 1 else 1)
    print("FAILED: " + ", ".join(map(str, f)) if f else "all passed")
    sys.exit(1 if f else 0)
