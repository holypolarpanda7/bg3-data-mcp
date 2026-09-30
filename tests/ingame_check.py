"""Ground-truth check: compare the index's resolved stats with what the running game actually loaded.

Needs BG3 running with a save loaded and Script Extender's console open. The deployed paks must match
the indexed layers (e.g. base + dnd55e: deploy the dnd55e release that matches ../dnd55e).

  uv run python tests/ingame_check.py --pid <bg3 pid> [--layers dnd55e] [--sample 150]

It samples entries, sends Lua through References/Dev/dnd55e-tools/se_inject.ps1 that prints each
entry's fields as JSON tagged [BG3DATA], reads them back from the newest Script Extender runtime log,
and reports field-level mismatches.
"""
import argparse
import glob
import json
import os
import random
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from bg3data import query, sources  # noqa: E402

INJECT = "/mnt/d/BG3Modding/Mod_Projects/References/Dev/dnd55e-tools/se_inject.ps1"
LOGDIR = "/mnt/c/Users/holyp/AppData/Local/Larian Studios/Baldur's Gate 3/Script Extender Logs"
SIMPLE = re.compile(r"^[A-Za-z0-9_ .:;,'()+\-*/%<>=!]*$")  # plain scalar-ish values that compare cleanly


def pick(store, active, n):
    names = [r[0] for r in store.db.execute(
        "SELECT DISTINCT name FROM stats WHERE type IN ('SpellData','StatusData','PassiveData','Character','Weapon','Armor')")]
    random.seed(20260930)
    random.shuffle(names)
    out = []
    for name in names:
        r = store.resolve(name, active)
        if not r:
            continue
        fields = {k: v for k, (v, _) in r["fields"].items()
                  if v and len(v) < 200 and SIMPLE.match(v) and k not in ("SpellType", "StatusType")}
        if len(fields) >= 3:
            keys = sorted(fields)[:8]
            out.append((name, {k: fields[k] for k in keys}))
        if len(out) >= n:
            break
    return out


def lua_lines(samples):
    lines = ["server"]
    for name, fields in samples:
        keys = ",".join(json.dumps(k) for k in fields)
        lines.append(
            f'local ok,s=pcall(Ext.Stats.Get,{json.dumps(name)}); local o={{n={json.dumps(name)},f={{}}}}; '
            f'if ok and s then for _,k in ipairs({{{keys}}}) do local okv,v=pcall(function() return s[k] end); '
            f'if okv then o.f[k]=(type(v)=="table") and Ext.Json.Stringify(v) or tostring(v) end end else o.missing=true end; '
            f'Ext.Utils.Print("[BG3DATA]"..Ext.Json.Stringify(o,{{Beautify=false}}))')
    return lines


def norm(v):
    return re.sub(r"[\s;]+", ";", str(v)).strip(";").lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--layers")
    ap.add_argument("--sample", type=int, default=150)
    a = ap.parse_args()
    store = query.Store(refresh=True, log=lambda m: print(m, file=sys.stderr))
    active = store.active(a.layers.split(",") if a.layers else [])
    samples = pick(store, active, a.sample)
    tmp = "/mnt/c/Users/holyp/AppData/Local/Temp/bg3data_ingame.txt"
    open(tmp, "w", newline="\r\n").write("\n".join(lua_lines(samples)))
    subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", sources.winpath(INJECT),
                    "-LinesFile", sources.winpath(tmp), "-ProcId", str(a.pid)], check=True)
    time.sleep(3)
    log = max(glob.glob(os.path.join(LOGDIR, "Extender Runtime*.log")), key=os.path.getmtime)
    got = {}
    for line in open(log, encoding="utf-8", errors="replace"):
        if "[BG3DATA]" in line:
            o = json.loads(line.split("[BG3DATA]", 1)[1])
            got[o["n"]] = o
    checked = mism = missing = 0
    bad = []
    for name, fields in samples:
        o = got.get(name)
        if not o or o.get("missing"):
            missing += 1
            bad.append(f"{name}: not loaded in game")
            continue
        for k, v in fields.items():
            checked += 1
            if norm(o["f"].get(k, "")) != norm(v):
                mism += 1
                bad.append(f"{name}.{k}: index={v!r} game={o['f'].get(k)!r}")
    print(f"layers {active}: {len(samples)} entries, {checked} fields compared, {mism} mismatches, {missing} entries missing in game")
    print("\n".join(bad[:40]))


if __name__ == "__main__":
    main()
