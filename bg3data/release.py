"""Local release of a mod layer (2026-10-06), after the gate (bg3data/gate.py) is green.

  python -m bg3data.release prepare LAYER VERSION   stamp Version64 in meta.lsx, add a CHANGELOG.md section, commit
  python -m bg3data.release package LAYER           gate must be GREEN on HEAD: pak + info.json -> zip + checksums
  python -m bg3data.release publish LAYER           tag v<version>, push it, GitHub Release with the zip (no mod sites yet)

Between prepare and package, run the gate on the release commit: a Version64-only meta.lsx change owes no builds, so it is
the quick static/regen/lint pass. The zip has the pak at its root plus info.json (BG3 Mod Manager / Vortex read the mod's
metadata from it), README.md, CHANGELOG.md and LICENSE when present - the layout Nexus Mods expects for a BG3 pak mod.
"""
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

from . import deploy, gate, sources


def version64(ver):
    """'1.2.3.4' (or v1.2.3) -> the game's int64: major<<55 | minor<<47 | revision<<31 | build."""
    p = [int(x) for x in ver.lstrip("v").split(".")]
    p += [0] * (4 - len(p))
    if len(p) != 4 or p[0] > 255 or p[1] > 255 or p[2] > 65535 or p[3] > 2147483647:
        raise ValueError(f"bad version {ver!r} (major.minor.revision[.build])")
    return (p[0] << 55) | (p[1] << 47) | (p[2] << 31) | p[3]


def version_str(v64):
    v = int(v64)
    return f"{v >> 55}.{(v >> 47) & 0xFF}.{(v >> 31) & 0xFFFF}.{v & 0x7FFFFFFF}"


def _meta(m):
    import glob
    return glob.glob(os.path.join(m["path"], "Mods", "*", "meta.lsx"))[0]


def stamp(meta_path, ver):
    text = open(meta_path, encoding="utf-8").read()
    head, sep, rest = text.partition('<node id="ModuleInfo">')
    if not sep:
        raise RuntimeError("meta.lsx has no ModuleInfo node")
    block_end = rest.find("<children>") if "<children>" in rest else len(rest)
    block, tail = rest[:block_end], rest[block_end:]
    new, n = re.subn(r'(<attribute id="Version64" type="int64" value=")\d+(")', rf"\g<1>{version64(ver)}\g<2>", block, count=1)
    if not n:
        raise RuntimeError("ModuleInfo has no Version64 attribute")
    open(meta_path, "w", encoding="utf-8", newline="").write(head + sep + new + tail)


def _last_tag(path):
    r = subprocess.run(["git", "describe", "--tags", "--abbrev=0", "--match", "v*"], cwd=path, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def changelog_section(path, ver, since=None):
    rng = f"{since}..HEAD" if since else "HEAD"
    subjects = gate._git(path, "log", "--no-merges", "--format=%ad %s", "--date=short", rng).splitlines()
    rows = []
    for s in subjects:
        d, _, msg = s.partition(" ")
        if msg.startswith(("Release v", "tests:")):
            continue
        rows.append(f"- {msg if len(msg) <= 220 else msg[:217] + '...'} ({d})")
    return f"## v{ver.lstrip('v')} - {time.strftime('%Y-%m-%d')}\n\n" + ("\n".join(rows) or "- maintenance") + "\n"


def prepare(layer, ver):
    _, m, info, _ = deploy.mod_info(layer)
    path = m["path"]
    if gate._git(path, "status", "--porcelain").strip():
        return "the mod's git tree has uncommitted changes - commit or stash them first"
    head = gate._git(path, "rev-parse", "HEAD").strip()
    r = gate.load_state(layer).get("runs", {}).get(head)
    if not (r and r.get("ok")):
        return f"the gate isn't GREEN on HEAD {head[:10]} ({r['desc'] if r else 'not gated'}) - run bg3_gate first"
    ver = ver.lstrip("v")
    stamp(_meta(m), ver)
    cl = os.path.join(path, "CHANGELOG.md")
    old = open(cl, encoding="utf-8").read() if os.path.exists(cl) else "# Changelog\n"
    title, _, body = old.partition("\n")
    open(cl, "w", encoding="utf-8").write(f"{title}\n\n{changelog_section(path, ver, _last_tag(path))}\n{body.lstrip()}")
    gate._git(path, "add", "CHANGELOG.md", os.path.relpath(_meta(m), path))
    gate._git(path, "commit", "-m", f"Release v{ver} ({time.strftime('%Y-%m-%d')})")
    return (f"stamped {info['Name']} v{ver} (Version64 {version64(ver)}), CHANGELOG.md section written, committed "
            f"{gate._git(path, 'rev-parse', '--short', 'HEAD').strip()}. Next: gate this commit (quick - a version stamp owes no builds), "
            f"then package.")


def package(layer):
    cfg, m, info, deps = deploy.mod_info(layer)
    path = m["path"]
    head = gate._git(path, "rev-parse", "HEAD").strip()
    r = gate.load_state(layer).get("runs", {}).get(head)
    if not (r and r.get("ok")):
        return f"the gate isn't GREEN on HEAD {head[:10]} ({r['desc'] if r else 'not gated'}) - not packaging"
    ver = version_str(info["Version64"])
    pak, n = deploy.pack(layer)
    out = os.path.join(sources.CACHE, "release", layer, f"v{ver}")
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    pak_name = info["Folder"] + ".pak"
    md5 = hashlib.md5(open(pak, "rb").read()).hexdigest()
    meta_text = open(_meta(m), encoding="utf-8").read()
    # XML attribute text: unescape it for info.json (mod managers show it as is - "Baldur&apos;s" once, 2026-10-07)
    attr = lambda k: html.unescape((re.search(rf'<node id="ModuleInfo">.*?<attribute id="{k}" type="\w+" value="([^"]*)"', meta_text, re.S) or [None, ""])[1])
    base = {"GustavDev", "GustavX", "Gustav", "Honour", "HonourX", "MainUI", "ModBrowser", "PhotoMode", "CrossplayUI"}
    info_json = {"Mods": [{"Author": attr("Author"), "Name": info["Name"], "Folder": info["Folder"], "Version": info["Version64"],
                           "Description": attr("Description"), "UUID": info["UUID"], "Created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                           "Dependencies": [d["UUID"] for d in deps if d.get("Name") not in base and not d.get("Name", "").startswith("DiceSet")],
                           "Group": info["UUID"]}], "MD5": md5}
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", os.path.basename(path.rstrip("/")))
    zip_path = os.path.join(out, f"{slug}_v{ver}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(pak, pak_name)
        z.writestr("info.json", json.dumps(info_json, indent=2))
        for f in ("README.md", "CHANGELOG.md", "LICENSE", "LICENSE.md"):
            if os.path.exists(os.path.join(path, f)):
                z.write(os.path.join(path, f), f)
    sums = {os.path.basename(zip_path): zip_path, pak_name: pak}
    with open(os.path.join(out, "checksums.txt"), "w") as fh:
        for name, p in sums.items():
            fh.write(f"SHA256 {hashlib.sha256(open(p, 'rb').read()).hexdigest()}  {name}\n")
    json.dump({"version": ver, "sha": head, "zip": zip_path, "pak_files": n, "pak_md5": md5},
              open(os.path.join(out, "release.json"), "w"), indent=1)
    return f"v{ver} packaged from {head[:10]}: {zip_path} ({os.path.getsize(zip_path) // 1024} KiB, pak {n} files, md5 {md5})"


def publish(layer):
    _, m, info, _ = deploy.mod_info(layer)
    path = m["path"]
    ver = version_str(info["Version64"])
    rel = os.path.join(sources.CACHE, "release", layer, f"v{ver}", "release.json")
    if not os.path.exists(rel):
        return f"no package for v{ver} - run package first"
    rj = json.load(open(rel))
    head = gate._git(path, "rev-parse", "HEAD").strip()
    if rj["sha"] != head:
        return f"the v{ver} package was built from {rj['sha'][:10]}, HEAD is {head[:10]} - package again"
    tag = f"v{ver}"
    cl = open(os.path.join(path, "CHANGELOG.md"), encoding="utf-8").read()
    notes = re.search(rf"## {re.escape(tag)} .*?(?=\n## |\Z)", cl, re.S)
    gate._git(path, "tag", "-a", tag, "-m", f"{info['Name']} {tag}")
    gate._git(path, "push", "origin", "HEAD", tag)
    d = os.path.dirname(rel)
    r = subprocess.run(["gh", "release", "create", tag, rj["zip"], os.path.join(d, "checksums.txt"), "--title", f"{info['Name']} {tag}",
                        "--notes", notes.group(0) if notes else tag], cwd=path, capture_output=True, text=True)
    if r.returncode != 0:
        return f"tagged and pushed {tag}, but the GitHub release failed: {r.stderr.strip()[:300]}"
    return f"published {tag}: {r.stdout.strip()} - upload {os.path.basename(rj['zip'])} to the Nexus page by hand (mod.io later)"


def main(argv):
    cmd, layer = argv[0], argv[1]
    if cmd == "prepare":
        print(prepare(layer, argv[2]))
    elif cmd == "package":
        print(package(layer))
    elif cmd == "publish":
        print(publish(layer))
    else:
        sys.exit("usage: python -m bg3data.release prepare LAYER VERSION | package LAYER | publish LAYER")


if __name__ == "__main__":
    main(sys.argv[1:])
