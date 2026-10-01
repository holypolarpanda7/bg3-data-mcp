"""Where things are, on whatever machine this runs on.

Runs natively on Windows or under WSL (the game, Script Extender, LSLib's Divine.exe and the console injector
are Windows programs either way). Everything machine-specific is discovered, and can be overridden in
layers.json:

  "base": {"game_data": "<install>/Data"}          game install (else: Steam libraries, GOG, common paths)
  "divine": "<path to Divine.exe>"                 LSLib (else: BG3_DIVINE env, PATH, common folders)
  "game": {"larian_dir": ..., "profile": "Public", "launcher": "auto|steam|gog|direct", "steam_exe": ...}

Config paths may be written Windows-style (D:\\...) or WSL-style (/mnt/d/...); both work on both.
"""
import functools
import glob
import os
import re
import shutil
import subprocess
import tempfile

IS_WINDOWS = os.name == "nt"
IS_WSL = not IS_WINDOWS and ("microsoft" in os.uname().release.lower() or bool(os.environ.get("WSL_DISTRO_NAME")))
STEAM_APP_ID = 1086940
GOG_GAME_ID = "1456460669"


# ------------------------------------------------------------------ paths
def to_win(path):
    """A path Windows programs (Divine.exe, PowerShell) can open."""
    if not path:
        return path
    if IS_WINDOWS:
        return os.path.abspath(path)
    if re.match(r"^[A-Za-z]:[\\/]", path):
        return path
    return subprocess.check_output(["wslpath", "-w", path], text=True).strip()


def to_native(path):
    """A path this process can open, from a Windows or WSL-style path."""
    if not path:
        return path
    path = os.path.expandvars(os.path.expanduser(path))
    if IS_WINDOWS:
        m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", path)
        return os.path.normpath(f"{m.group(1).upper()}:/{m.group(2)}") if m else os.path.normpath(path)
    if re.match(r"^[A-Za-z]:[\\/]", path):
        if IS_WSL:
            return subprocess.check_output(["wslpath", "-u", path.replace("\\", "/")], text=True).strip()
        return path
    return path


@functools.lru_cache(maxsize=None)
def win_env(name):
    """A Windows environment variable (LOCALAPPDATA, TEMP, USERPROFILE...), as a native path."""
    if IS_WINDOWS:
        return os.environ.get(name, "")
    if not IS_WSL:
        return ""
    try:
        out = subprocess.run(["cmd.exe", "/c", f"echo %{name}%"], capture_output=True, text=True, timeout=15,
                             cwd="/mnt/c" if os.path.isdir("/mnt/c") else None).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return to_native(out) if out and "%" not in out else ""


def reg_query(key, value):
    """A REG_SZ value from the Windows registry, or None."""
    try:
        out = subprocess.run(["reg.exe" if not IS_WINDOWS else "reg", "query", key, "/v", value],
                             capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(rf"^\s*{re.escape(value)}\s+REG_\w+\s+(.+?)\s*$", out, re.M | re.I)
    return m.group(1) if m else None


def windows_temp():
    """A temp folder both this process and Windows programs can read (for injector line files, staging)."""
    if IS_WINDOWS:
        return tempfile.gettempdir()
    t = win_env("TEMP")
    return t if t and os.path.isdir(t) else tempfile.gettempdir()


def cache_dir():
    if os.environ.get("BG3_DATA_CACHE"):
        return os.environ["BG3_DATA_CACHE"]
    if IS_WINDOWS:
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "bg3-data-mcp", "cache")
    # keep the index on the Linux filesystem under WSL (SQLite on /mnt/* drives is slow and lock-prone)
    return os.path.join(os.path.expanduser("~"), ".cache", "bg3-data-mcp", "cache")


# ------------------------------------------------------------------ discovery
def _vdf_paths(vdf_text):
    return [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', vdf_text)]


def steam_dir():
    for key, val in ((r"HKCU\Software\Valve\Steam", "SteamPath"), (r"HKLM\SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                     (r"HKLM\SOFTWARE\Valve\Steam", "InstallPath")):
        v = reg_query(key, val)
        if v and os.path.isdir(to_native(v)):
            return to_native(v)
    for guess in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"):
        if os.path.isdir(to_native(guess)):
            return to_native(guess)
    return None


def find_game():
    """(install dir, store) of Baldur's Gate 3: Steam libraries, then GOG, then common folders."""
    sd = steam_dir()
    if sd:
        libs = [sd]
        vdf = os.path.join(sd, "steamapps", "libraryfolders.vdf")
        if os.path.exists(vdf):
            libs += [to_native(p) for p in _vdf_paths(open(vdf, encoding="utf-8", errors="replace").read())]
        for lib in dict.fromkeys(libs):
            acf = os.path.join(lib, "steamapps", f"appmanifest_{STEAM_APP_ID}.acf")
            if os.path.exists(acf):
                m = re.search(r'"installdir"\s+"([^"]+)"', open(acf, encoding="utf-8", errors="replace").read())
                d = os.path.join(lib, "steamapps", "common", m.group(1) if m else "Baldurs Gate 3")
                if os.path.isdir(d):
                    return d, "steam"
    for key in (rf"HKLM\SOFTWARE\WOW6432Node\GOG.com\Games\{GOG_GAME_ID}", rf"HKLM\SOFTWARE\GOG.com\Games\{GOG_GAME_ID}"):
        v = reg_query(key, "path")
        if v and os.path.isdir(to_native(v)):
            return to_native(v), "gog"
    for guess in (r"C:\GOG Games\Baldur's Gate 3", r"C:\Program Files (x86)\GOG Galaxy\Games\Baldur's Gate 3",
                  r"C:\Program Files (x86)\Steam\steamapps\common\Baldurs Gate 3"):
        if os.path.isdir(to_native(guess)):
            return to_native(guess), "gog" if "GOG" in guess else "steam"
    return None, None


def larian_dir(cfg=None):
    """%LOCALAPPDATA%\\Larian Studios\\Baldur's Gate 3: Mods, PlayerProfiles, Script Extender Logs."""
    g = (cfg or {}).get("game") or {}
    if g.get("larian_dir"):
        return to_native(g["larian_dir"])
    base = win_env("LOCALAPPDATA")
    return os.path.join(base, "Larian Studios", "Baldur's Gate 3") if base else None


def find_divine(cfg=None):
    """LSLib's Divine.exe (v1.20.4+; Vortex's bundled copy is too old for current LSF files - last resort)."""
    cands = [(cfg or {}).get("divine"), os.environ.get("BG3_DIVINE"), shutil.which("Divine.exe"), shutil.which("divine.exe")]
    home = win_env("USERPROFILE")
    for root in filter(None, [home, to_native("C:\\"), to_native("D:\\")]):
        cands += glob.glob(os.path.join(root, "*", "LSLib", "Packed", "Tools", "Divine.exe"))
        cands += glob.glob(os.path.join(root, "*", "Tools", "LSLib", "Packed", "Tools", "Divine.exe"))
        cands += glob.glob(os.path.join(root, "*", "*", "Tools", "LSLib", "Packed", "Tools", "Divine.exe"))
    for c in cands:
        if c and os.path.isfile(to_native(c)):
            return to_native(c)
    vortex = to_native(r"C:\Program Files\Black Tree Gaming Ltd\Vortex\resources\app.asar.unpacked\bundledPlugins\game-baldursgate3\tools\divine.exe")
    return vortex if os.path.isfile(vortex) else None


def game_exe(install):
    for exe in ("bg3_dx11.exe", "bg3.exe"):
        p = os.path.join(install, "bin", exe)
        if os.path.isfile(p):
            return p
    return None


# ------------------------------------------------------------------ processes
def run_win(args, **kw):
    """Run a Windows program; under WSL the cwd must be a Windows drive or interop warns."""
    if IS_WSL and "cwd" not in kw and os.path.isdir("/mnt/c"):
        kw["cwd"] = "/mnt/c"
    return subprocess.run(args, capture_output=True, text=True, **kw)


def tasklist():
    try:
        return run_win(["tasklist.exe" if IS_WSL else "tasklist"], timeout=20).stdout.lower()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def not_responding(images=("bg3_dx11.exe", "bg3.exe")):
    """Images whose window Windows reports as Not Responding (a hung load looks like this, not like a crash)."""
    try:
        out = run_win(["tasklist.exe" if IS_WSL else "tasklist", "/fi", "STATUS eq NOT RESPONDING"], timeout=20).stdout.lower()
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [i for i in images if i in out]


def launch_detached(exe, args):
    """Start a Windows program without waiting for it."""
    if IS_WINDOWS:
        subprocess.Popen([exe] + list(args), creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                         close_fds=True)
    else:
        run_win(["cmd.exe", "/c", "start", "", to_win(exe)] + list(args), timeout=30)


def shell_command(cmd):
    """argv to run a mod's custom deploy command: bash where available (WSL, Git Bash), else PowerShell."""
    if not IS_WINDOWS:
        return ["bash", "-lc", cmd]
    bash = shutil.which("bash")
    return [bash, "-lc", cmd] if bash else ["powershell", "-NoProfile", "-Command", cmd]


# ------------------------------------------------------------------ mod managers
def mod_managers(cfg=None):
    """What manages the game's Mods folder / load order, and what that means for a pak deployed by hand."""
    out = []
    ld = larian_dir(cfg)
    mods = os.path.join(ld, "Mods") if ld else None
    if mods and os.path.exists(os.path.join(mods, "vortex.deployment.json")):
        import json
        try:
            man = json.load(open(os.path.join(mods, "vortex.deployment.json"), encoding="utf-8"))
            managed = [f.get("relPath") for f in man.get("files", [])]
        except (OSError, ValueError):
            managed = []
        out.append({"name": "Vortex", "managed": managed,
                    "note": "deploys paks as links and can rewrite modsettings.lsx on deploy; paks it doesn't manage are left in place "
                            "but disabled if Vortex writes the load order - re-run bg3_deploy (it re-enables the mod) after a Vortex deploy"})
    for exe in ("BG3ModManager.exe",):
        if exe.lower() in tasklist():
            out.append({"name": "BG3 Mod Manager", "managed": [],
                        "note": "exporting the load order rewrites modsettings.lsx; add the mod in BG3MM too, or re-run bg3_deploy after exporting"})
    if ld and os.path.isdir(os.path.join(ld, "Mods")) and glob.glob(os.path.join(ld, "Mods", "*.pak")) and not out:
        out.append({"name": "none detected (in-game mod manager or manual)", "managed": [],
                    "note": "the in-game manager lists local paks under Installed; bg3_deploy enables the mod in modsettings.lsx"})
    return out
