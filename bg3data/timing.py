"""Per-user timing settings (2026-10-06): the waits and poll intervals of the in-game driver, test harness and console bridge.

Machines differ - a slower PC needs longer UI settle waits, a fast one can poll tighter - so every user of the MCP sets their own
in a JSON file outside the repo:

  ~/.config/bg3-data-mcp/timing.json   (override the path with BG3_DATA_TIMING)
  {
    "scale": 1.0,                    every UI / harness wait is multiplied by this (clamped to SCALE_RANGE)
    "min_wait": 0.0,                 no scaled wait is shorter than this (seconds)...
    "max_wait": 30.0,                ...or longer than this
    "settings": {"se.poll_s": 0.02, "build.checkpoint_every": 5}   named settings, each clamped to its own range
  }

Defaults reproduce the behaviour verified on the reference machine (RTX 3080 Ti, 32 GB, 2026-10-06). bg3_timing shows the
effective values and can calibrate: it measures this machine's console and screen-helper round trips and suggests a scale.
"""
import json
import os
import time

PATH = os.environ.get("BG3_DATA_TIMING", os.path.join(os.path.expanduser("~"), ".config", "bg3-data-mcp", "timing.json"))
SCALE_RANGE = (0.25, 4.0)

# name -> (default, min, max, what it is)
SETTINGS = {
    "se.poll_s": (0.02, 0.005, 0.5, "how often a console call checks for its result file"),
    "se.log_poll_s": (0.05, 0.01, 0.5, "how often a console call that needs printed output checks the log"),
    "se.line_gap_ms": (20, 5, 800, "pause after each line typed into the game console"),
    "build.pre_levelup_s": (1.0, 0.0, 5.0, "pause after granting XP before opening the level-up screen"),
    "build.checkpoint_every": (5, 0, 19, "save a checkpoint every N levels in a build (0 = never)"),
    "build.xp_poll_s": (0.25, 0.05, 2.0, "how often the XP grant checks it landed"),
}

_CACHE = {"at": 0.0, "mtime": None, "data": {}}


def _load():
    """The user's file, re-read when it changes (checked at most every 2 s)."""
    now = time.time()
    if now - _CACHE["at"] < 2.0:
        return _CACHE["data"]
    _CACHE["at"] = now
    try:
        mt = os.path.getmtime(PATH)
    except OSError:
        _CACHE.update(mtime=None, data={})
        return {}
    if mt != _CACHE["mtime"]:
        try:
            with open(PATH, encoding="utf-8") as f:
                data = json.load(f)
            _CACHE.update(mtime=mt, data=data if isinstance(data, dict) else {})
        except (OSError, ValueError):
            _CACHE.update(mtime=mt, data={})
    return _CACHE["data"]


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def scale():
    try:
        return _clamp(float(_load().get("scale", 1.0)), *SCALE_RANGE)
    except (TypeError, ValueError):
        return 1.0


def get(name):
    """A named setting: the user's value clamped to its range, else the default."""
    default, lo, hi, _ = SETTINGS[name]
    v = (_load().get("settings") or {}).get(name, default)
    try:
        v = _clamp(type(default)(v), lo, hi)
    except (TypeError, ValueError):
        v = default
    return v


def secs(base):
    """A UI / harness wait of `base` seconds on the reference machine, scaled for this one and clamped to min_wait..max_wait."""
    d = _load()
    try:
        lo, hi = float(d.get("min_wait", 0.0)), float(d.get("max_wait", 30.0))
    except (TypeError, ValueError):
        lo, hi = 0.0, 30.0
    return _clamp(base * scale(), lo, hi)


def wait(base):
    """time.sleep for a UI / harness wait (see secs)."""
    time.sleep(secs(base))


def describe():
    d = _load()
    lines = [f"timing settings: {PATH}" + ("" if os.path.exists(PATH) else " (not present - defaults)"),
             f"  scale = {scale()} (range {SCALE_RANGE[0]}-{SCALE_RANGE[1]}); min_wait = {d.get('min_wait', 0.0)}, "
             f"max_wait = {d.get('max_wait', 30.0)}"]
    for k, (default, lo, hi, what) in SETTINGS.items():
        v = get(k)
        lines.append(f"  {k} = {v}{'' if v == default else f' (default {default})'}  [{lo}-{hi}]  {what}")
    return "\n".join(lines)


def save(scale_=None, settings=None, min_wait=None, max_wait=None):
    """Write the user's file (merging with what's there); values are clamped when read, not here."""
    d = dict(_load()) if os.path.exists(PATH) else {}
    if scale_ is not None:
        d["scale"] = float(scale_)
    if min_wait is not None:
        d["min_wait"] = float(min_wait)
    if max_wait is not None:
        d["max_wait"] = float(max_wait)
    if settings:
        unknown = [k for k in settings if k not in SETTINGS]
        if unknown:
            raise ValueError(f"unknown timing setting(s) {unknown}; known: {', '.join(SETTINGS)}")
        d.setdefault("settings", {}).update(settings)
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    _CACHE["at"] = 0.0
    return describe()
