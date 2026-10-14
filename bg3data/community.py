"""Daily community watch: new mod.io comments that read like bug / 'not working' reports on the user's mods -> a Discord channel.

  bg3-data watch-community [dry] [seed]

Each run reads the newest comments of every layer with a "modio" block in layers.json, drops the author's own, and posts
the ones not seen before AND matching BUG (other comments are recorded as seen, not posted) to #mod-bug-reports as one digest. State: ~/.cache/bg3-data-mcp/community-watch.json (seen
comment ids per layer); the first run for a layer only records what is already there ("seed" forces that for all layers).
Nexus has no comment or bug API (verified 2026-10-13) - that part would need the signed-in browser (nexus_web.py).
"""
import json
import os
import re
import sys
import time

from . import modio, sources, watch

STATE = os.path.expanduser("~/.cache/bg3-data-mcp/community-watch.json")
LOG = os.path.expanduser("~/.cache/bg3-data-mcp/community-watch.log")
CHANNEL = "mod-bug-reports"
OWN = {"GreygorVangar"}
# A comment is reported only when it reads like a problem report (keyword heuristic - praise and questions are skipped).
BUG = re.compile(r"\b(bugs?|bugged|glitch\w*|crash\w*|broken|breaks?|not work\w*|n'?t work\w*|no longer work\w*|stopped working|"
                 r"does ?n'?t (?:show|appear|load|apply|function|trigger)|not (?:show|appear|load|apply|trigger|function)\w*|"
                 r"errors?|fail\w*|missing|greyed|grayed|gray out|grey out|inactive|disappear\w*|freez\w*|stuck|issues?|"
                 r"problems?|incompatib\w*|conflict\w*|not provid\w*|won'?t)\b", re.I)


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with open(LOG, "a") as f:
        f.write(line + "\n")
    print(line, file=sys.stderr)


def fresh_comments(layer, seen):
    e = modio.entry(layer)
    r = modio.call("GET", f"/games/{e['game']}/mods/{e['mod']}/comments", e["game"], query={"_limit": 100, "_sort": "-date_added"})
    cs = r.get("data", [])
    return [c for c in cs if c["id"] not in seen], [c["id"] for c in cs]


def run(dry=False, seed=False):
    cfg = sources.load_config()
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    lines, notes = [], []
    for m in cfg["mods"]:
        layer = m["name"]
        if not m.get("modio"):
            continue
        key = "modio:" + layer
        new, ids = fresh_comments(layer, set(state.get(key, [])))
        first = key not in state or seed
        state[key] = sorted(set(state.get(key, [])) | set(ids))
        theirs = [c for c in new if (c.get("user") or {}).get("username") not in OWN and BUG.search(c.get("content") or "")]
        if first:
            notes.append(f"{layer}: seeded {len(ids)} existing comment(s)")
            continue
        if theirs:
            url = (modio.call("GET", f"/games/{m['modio']['game']}/mods/{m['modio']['mod']}", m["modio"]["game"]) or {}).get("profile_url", "")
            lines.append(f"**{layer}** (mod.io) - {len(theirs)} bug report(s) {url}")
            for c in reversed(theirs):
                body = (c.get("content") or "").strip().replace("\n", " ")
                lines.append(f"> **{(c.get('user') or {}).get('username')}**{' (reply)' if c.get('reply_id') else ''}: {body[:350]}")
    msg = "\n".join(lines)
    if lines and not dry:
        watch.notify({"watch": {"discord_channel": CHANNEL}}, "New mod.io bug reports:\n" + msg)
    if not dry:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump(state, open(STATE, "w"), indent=1)
    out = "; ".join(notes) or ("nothing new" if not lines else "posted" if not dry else "dry run:\n" + msg)
    log(out)
    return out


def main(args):
    print(run(dry="dry" in args, seed="seed" in args))
    return 0
