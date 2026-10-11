"""Watch upstream dnd55e for BREAKING changes to a mod built on it (default: Path to Apotheosis), notify a Discord channel.

  bg3-data watch-dnd55e [dry] [force]

Upstream publishes a release as a commit whose subject is a bare version ("4.13.4.1"). Each run: fetch the local dnd55e clone
(fetch only - no checkout, the clone's files are never touched), find the newest version commit, and - when it is new - diff
the mod's baseline release (the version recorded in the mod's bg3deps.lock.json) against it over Public/ and Mods/.
Breaking = an entity the baseline DEFINED (stat `new entry`, or a GUID key in an LSX node) that the new version no longer
defines anywhere AND that the mod overrides or references (tokens from the indexed mod layer, as bg3_deps_diff uses).
A Discord message is posted only for breaking changes, and only when the set of breaking entities changed since the last post.
State: ~/.cache/bg3-data-mcp/dnd55e-watch.json; log: dnd55e-watch.log beside it.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

from . import deps, platform, query, sources

STATE = os.path.expanduser("~/.cache/bg3-data-mcp/dnd55e-watch.json")
LOG = os.path.expanduser("~/.cache/bg3-data-mcp/dnd55e-watch.log")
VERSION_RE = r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$"
DEF_STAT = re.compile(r'^new entry "([^"]+)"')
DEF_GUID = re.compile(r'id="(?:UUID|MapKey|Name|TableUUID|Object)"[^>]*value="([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"')
STAT_FILE = re.compile(r"/Stats/Generated/Data/.*\.txt$")


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with open(LOG, "a") as f:
        f.write(line + "\n")
    print(line, file=sys.stderr)


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, errors="replace")
    if r.returncode:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {r.stderr.strip()[:300]}")
    return r.stdout


def clone_path(cfg):
    m = next((x for x in cfg["mods"] if x["name"] == "dnd55e"), None)
    p = (cfg.get("watch") or {}).get("dnd55e_clone") or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "dnd55e")
    if not os.path.isdir(os.path.join(p, ".git")):
        raise RuntimeError(f"dnd55e git clone not found at {p} (set watch.dnd55e_clone in layers.json); layer path {m and m['path']}")
    return p


def newest_version_commit(repo, ref="origin/main"):
    out = git(repo, "log", ref, "--format=%H %s", "-E", f"--grep={VERSION_RE}", "-1").strip()
    return tuple(out.split(" ", 1)) if out else (None, None)


def commit_for(repo, version, ref="origin/main"):
    out = git(repo, "log", ref, "--format=%H", "-E", f"--grep=^{re.escape(version)}$", "-1").strip()
    return out or None


def _defs(diff_text):
    """(removed, added) sets of defined entity keys from a -U0 diff of Public/ and Mods/."""
    rem, add, cur = set(), set(), ""
    for ln in diff_text.splitlines():
        if ln.startswith("+++ "):
            cur = ln[6:]
            continue
        if ln.startswith(("--- ", "@@", "diff ", "index ")) or not ln or ln[0] not in "+-":
            continue
        body, bucket = ln[1:], rem if ln[0] == "-" else add
        m = DEF_STAT.match(body.strip()) if STAT_FILE.search("/" + cur) else None
        if m:
            bucket.add(m.group(1))
        for g in DEF_GUID.findall(body):
            bucket.add(g)
    return rem, add


def breaking(repo, base, new, layer):
    diff = git(repo, "diff", "-U0", "--no-color", "--diff-filter=ACDMR", f"{base}", f"{new}", "--", "Public", "Mods")
    rem, add = _defs(diff)
    gone = rem - add
    s = query.Store(refresh=False, log=lambda m: None)
    mod_tokens = deps._mod_tokens(s.db, layer)
    hits = sorted(k for k in gone if k in mod_tokens)
    return hits, len(gone), len(diff)


def notify(cfg, text):
    env = {}
    for ln in open(os.path.expanduser("~/.config/discord-mcp/env")):
        if "=" in ln and not ln.startswith("#"):
            k, v = ln.strip().split("=", 1)
            env[k] = v
    ch = (cfg.get("watch") or {}).get("discord_channel_id")
    hdr = {"Authorization": f"Bot {env['DISCORD_TOKEN']}", "User-Agent": "DiscordBot (https://github.com/holypolarpanda7/bg3-data-mcp, 1.0)", "Content-Type": "application/json"}
    if not ch:
        name = (cfg.get("watch") or {}).get("discord_channel", "github-notifications")
        req = urllib.request.Request(f"https://discord.com/api/v10/guilds/{env['DISCORD_GUILD_ID']}/channels", headers=hdr)
        ch = next(c["id"] for c in json.load(urllib.request.urlopen(req, timeout=30)) if c["name"] == name)
    req = urllib.request.Request(f"https://discord.com/api/v10/channels/{ch}/messages", method="POST", headers=hdr,
                                 data=json.dumps({"content": text[:1900]}).encode())
    urllib.request.urlopen(req, timeout=30).read()


def run(layer_name=None, dry=False, force=False):
    cfg = sources.load_config()
    layer = layer_name or next(m["name"] for m in cfg["mods"] if "Path-to-Apotheosis" in m["path"])
    repo = clone_path(cfg)
    git(repo, "fetch", "-q", "origin")
    head, head_ver = newest_version_commit(repo)
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    if not head:
        return "no version commit found upstream"
    if head == state.get("checked") and not force:
        return f"no new dnd55e release (still {head_ver})"
    lock = deps.read_lock(cfg, layer)
    dep = next((v for v in lock["deps"].values() if v.get("layer") == "dnd55e"), None)
    if not dep:
        return f"{layer} has no dnd55e entry in its bg3deps.lock.json (run bg3_deps_lock)"
    base = commit_for(repo, dep["version"])
    if not base:
        return f"baseline dnd55e {dep['version']} has no version commit upstream - cannot diff"
    hits, n_gone, _ = breaking(repo, base, head, layer)
    state["checked"] = head
    sig = hashlib.sha1("\n".join(hits).encode()).hexdigest()
    msg = None
    if hits and sig != state.get("notified_sig"):
        shown = ", ".join(f"`{h}`" for h in hits[:15]) + (f" (+{len(hits) - 15} more)" if len(hits) > 15 else "")
        msg = (f"**dnd55e {dep['version']} -> {head_ver}: breaking for {layer}**\n"
               f"{len(hits)} entit{'y' if len(hits) == 1 else 'ies'} {layer} overrides or references no longer exist upstream: {shown}\n"
               f"https://github.com/Yoonmoonsik/dnd55e/commit/{head}")
    result = (f"dnd55e {dep['version']} -> {head_ver}: {n_gone} entities removed upstream, {len(hits)} that {layer} uses"
              + (": " + ", ".join(hits[:20]) if hits else " - not breaking"))
    if msg:
        if dry:
            result += "\n[dry run] would post:\n" + msg
        else:
            notify(cfg, msg)
            state["notified_sig"] = sig
            result += "\nposted to Discord"
    if not dry:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump(state, open(STATE, "w"), indent=1)
    return result


def main(argv):
    try:
        out = run(dry="dry" in argv, force="force" in argv)
    except Exception as e:  # a scheduled run must not die silently
        log(f"ERROR {type(e).__name__}: {e}")
        return 1
    log(out.splitlines()[0])
    print(out)
    return 0
