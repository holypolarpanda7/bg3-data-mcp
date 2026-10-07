"""Drive the Larian Toolkit (Glasses.exe) for packaging and mod.io publishing (2026-10-07).

  python -m bg3data.toolkit_ui state
  python -m bg3data.toolkit_ui projects
  python -m bg3data.toolkit_ui open LAYER                 launch the Toolkit if needed and open the layer's project
  python -m bg3data.toolkit_ui publish-local LAYER        Project Settings -> Publish Local: the Toolkit packs the pak into Mods/
  python -m bg3data.toolkit_ui publish LAYER [--apply]    Project Settings -> Publish (mod.io); dry run without --apply

How (found 2026-10-07 on Toolkit 4.1.1.6931813): the project picker (RadioButton rows, AutomationId m_OpenButton) and the
menus (Project > Project Settings...) are WPF and driven through UI Automation. The Project Settings window renders its own
controls (no UIA children), so its buttons are clicked at offsets from the window's bottom edge (its UIA rectangle) and the
outcome is checked from outside: Publish Local writes <Mods>/<Folder>.pak (its mtime), Publish opens the browser on the mod's
mod.io File Manager page (a new window with "mod.io" in its title). A screenshot of the Settings window is saved each step.
The version comes from the mod's meta.lsx (bg3data.release prepare stamps it) - nothing is typed into the Toolkit. Packing
REWRITES meta.lsx (verified 2026-10-07): dependency MD5s / versions / names refreshed, and with Auto-increment ticked the mod's
own build number +1 per Publish Local / Publish. Both commands report that diff (meta_changed) - commit it after a real
publish, revert it after a test.
Publishing is outward-facing: it needs apply=True and a GREEN gate on the mod's HEAD.
"""
import json
import os
import sys
import time

from . import platform, sources

PS = os.path.join(os.path.dirname(__file__), "ps", "toolkitui.ps1")
STEAM_APP = "2934770"            # Baldur's Gate 3 Toolkit
SETTINGS = "Project Settings"
# Project Settings buttons as (x from the left edge, y from the bottom edge) of the window, measured on an 820x815 window
BUTTONS = {"publish_local": (64, 28), "publish": (164, 28), "cancel": ("r", 164, 28), "save": ("r", 60, 28)}


def _ps(cmd, timeout=120, **kw):
    args = ["powershell.exe" if platform.IS_WSL else "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", platform.to_win(PS), "-Cmd", cmd]
    for k, v in kw.items():
        args += [f"-{k[0].upper()}{k[1:]}", str(v)]
    r = platform.run_win(args, timeout=timeout)
    out = (r.stdout or "").strip().splitlines()
    try:
        return json.loads(out[-1]) if out else {"error": (r.stderr or "no output").strip()[:300]}
    except json.JSONDecodeError:
        return {"error": ("\n".join(out) or r.stderr or "")[-300:]}


def _shot_path(tag):
    d = os.path.join(sources.CACHE, "toolkit")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{time.strftime('%Y%m%d-%H%M%S')}-{tag}.png")


def screenshot(tag="screen", window=None):
    p = _shot_path(tag)
    r = _ps("shot", out=platform.to_win(p), title=window or "")
    return p if r.get("ok") else None


def state():
    return _ps("state")


def _wait(pred, timeout, step=3.0):
    t = time.time()
    while time.time() - t < timeout:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return None


def launch(timeout=240):
    """Start the Toolkit through Steam if it isn't running; wait for the project picker or an open project."""
    st = state()
    if st.get("running"):
        return st
    platform.run_win(["cmd.exe" if platform.IS_WSL else "cmd", "/c", "start", "", f"steam://rungameid/{STEAM_APP}"], timeout=30)
    st = _wait(lambda: (lambda s: s if s.get("running") and (s.get("picker") or " - " in (s.get("title") or "")) else None)(state()),
               timeout)
    return st or {"error": f"the Toolkit didn't come up in {timeout}s"}


def project_name(layer):
    from . import deploy
    _, _, info, _ = deploy.mod_info(layer)
    return info["Name"], info["Folder"]


def open_project(layer, timeout=420):
    """The layer's project open in the Toolkit (its meta.lsx Name is the picker row and the window title)."""
    name, _ = project_name(layer)
    st = launch()
    if st.get("error"):
        return st
    if f" - {name} - " in (st.get("title") or ""):
        return {"ok": True, "project": name, "already": True}
    if not st.get("picker"):
        return {"error": f"another project is open ({st.get('title')}) - close the Toolkit first"}
    r = _ps("open", project=name)
    if r.get("error"):
        return r
    st = _wait(lambda: (lambda s: s if f" - {name} - " in (s.get("title") or "") else None)(state()), timeout, 5)
    return {"ok": True, "project": name} if st else {"error": f"{name} didn't finish opening in {timeout}s"}


def open_settings(timeout=30):
    st = state()
    if st.get("settings"):
        return st["settings"]
    r = _ps("menu", menu="Project", item="Project Settings...")
    if r.get("error"):
        return r
    rect = _wait(lambda: state().get("settings"), timeout, 1.5)
    return rect or {"error": "the Project Settings window didn't open"}


def _button(rect, which):
    b = BUTTONS[which]
    if b[0] == "r":
        return rect["x"] + rect["w"] - b[1], rect["y"] + rect["h"] - b[2]
    return rect["x"] + b[0], rect["y"] + rect["h"] - b[1]


def publish_local(layer, timeout=180):
    """Project Settings -> Publish Local; returns the pak the Toolkit wrote (checked by its mtime), with a screenshot."""
    from . import deploy
    name, folder = project_name(layer)
    r = open_project(layer)
    if r.get("error"):
        return r
    rect = open_settings()
    if "error" in rect:
        return rect
    cfg = sources.load_config()
    _, mods_dir, _ = deploy._paths(cfg)
    pak = os.path.join(mods_dir, folder + ".pak")
    before = os.path.getmtime(pak) if os.path.exists(pak) else 0
    x, y = _button(rect, "publish_local")
    _ps("click", x=x, y=y)
    ok = _wait(lambda: os.path.exists(pak) and os.path.getmtime(pak) > before, timeout, 2)
    time.sleep(2)
    _ps("closeexplorer", location="Mods")   # the Toolkit opens Explorer on the Mods folder afterwards
    shot = screenshot("publish-local", SETTINGS)
    if not ok:
        return {"error": f"no new {folder}.pak in {mods_dir} after {timeout}s", "screenshot": shot}
    return {"ok": True, "pak": pak, "bytes": os.path.getsize(pak), "screenshot": shot, "meta_changed": _meta_diff(layer)}


def _meta_diff(layer):
    from . import deploy, gate
    _, m, _, _ = deploy.mod_info(layer)
    return gate._git(m["path"], "diff", "--stat", "--", "Mods", "Projects", check=False).strip() or None


def _gate_green(layer):
    from . import gate, deploy
    _, m, _, _ = deploy.mod_info(layer)
    head = gate._git(m["path"], "rev-parse", "HEAD").strip()
    r = gate.load_state(layer).get("runs", {}).get(head)
    return head, bool(r and r.get("ok")), (r or {}).get("desc", "not gated")


def publish(layer, apply=False, timeout=900):
    """Project Settings -> Publish (mod.io). Dry run unless apply=True: opens the project and the settings, saves a screenshot
    of what would be published, and stops. With apply: needs a GREEN gate on HEAD, clicks Publish, and waits for the browser to
    open on mod.io (the Toolkit's own sign that the upload finished)."""
    name, _ = project_name(layer)
    head, green, desc = _gate_green(layer)
    if apply and not green:
        return {"error": f"the gate isn't GREEN on {layer} HEAD {head[:10]} ({desc}) - not publishing"}
    r = open_project(layer)
    if r.get("error"):
        return r
    rect = open_settings()
    if "error" in rect:
        return rect
    shot = screenshot("publish-settings", SETTINGS)
    if not apply:
        return {"dry_run": True, "project": name, "gate": f"{'GREEN' if green else 'not green'} at {head[:10]} ({desc})",
                "screenshot": shot, "note": "check name, description, version and thumbnail in the screenshot; apply=True publishes"}
    before = {w["name"] for w in _ps("windows").get("windows", [])}
    x, y = _button(rect, "publish")
    _ps("click", x=x, y=y)
    seen = _wait(lambda: [w["name"] for w in _ps("windows").get("windows", [])
                          if w["name"] not in before and "mod.io" in w["name"].lower()], timeout, 5)
    after = screenshot("publish-after")
    if not seen:
        return {"error": f"no mod.io browser window within {timeout}s - check the Toolkit's Message Log", "screenshot": after}
    return {"ok": True, "project": name, "browser": seen[0], "screenshot": after, "meta_changed": _meta_diff(layer),
            "next": "on mod.io: wait for the scan, fill the profile if new, then Go live"}


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m bg3data.toolkit_ui")
    ap.add_argument("cmd", choices=["state", "projects", "open", "publish-local", "publish", "screenshot"])
    ap.add_argument("layer", nargs="?")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "state":
        r = state()
    elif a.cmd == "projects":
        r = _ps("projects")
    elif a.cmd == "screenshot":
        r = {"screenshot": screenshot("screen")}
    elif a.cmd == "open":
        r = open_project(a.layer)
    elif a.cmd == "publish-local":
        r = publish_local(a.layer)
    else:
        r = publish(a.layer, apply=a.apply)
    print(json.dumps(r, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
