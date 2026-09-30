"""Ground truth: compare the index's resolved stats with what the RUNNING game loaded (via bg3data.se).

  uv run python tests/ingame_check.py [--layers dnd55e] [--sample 300] [--exclude-layer apotheosis]

Pick --layers to match what's deployed. Entries defined by --exclude-layer are skipped (e.g. an
outdated deployed pak). Writes tests/INGAME_REPORT.md.
"""
import argparse
import collections
import os
import random
import sys
import time
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from bg3data import query, se  # noqa: E402

TYPES = ("SpellData", "StatusData", "PassiveData", "Character", "Weapon", "Armor", "Object", "InterruptData")
SKIP_FIELDS = {"SpellType", "StatusType"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layers", default="dnd55e")
    ap.add_argument("--sample", type=int, default=300)
    ap.add_argument("--exclude-layer", default="apotheosis")
    ap.add_argument("--batch", type=int, default=50)
    a = ap.parse_args()
    started = datetime.now().astimezone()
    store = query.Store(refresh=False)
    active = store.active([l for l in a.layers.split(",") if l])
    excluded = {r[0] for r in store.db.execute("SELECT DISTINCT name FROM stats WHERE layer=?", (a.exclude_layer,))}
    names = [r[0] for r in store.db.execute(
        f"SELECT DISTINCT name FROM stats WHERE type IN ({','.join('?' * len(TYPES))}) AND layer IN ({','.join('?' * len(active))})",
        list(TYPES) + active) if r[0] not in excluded]
    random.seed(20260930)
    random.shuffle(names)
    # bias towards entries a mod touches, where resolution rules matter most
    touched = [n for n in names if len(store.defs(n, active)) > 1]
    pick = (touched[: a.sample // 2] + [n for n in names if n not in set(touched)])[: a.sample]
    resolved = {n: store.resolve(n, active) for n in pick}
    resolved = {n: r for n, r in resolved.items() if r}
    t0 = time.time()
    live = {}
    items = list(resolved.items())
    for i in range(0, len(items), a.batch):
        chunk = {n: sorted(k for k in r["fields"] if k not in SKIP_FIELDS) for n, r in items[i:i + a.batch]}
        live.update(se.live_stats_many(chunk, timeout=90))
    elapsed = time.time() - t0
    fields = mism = uncomparable = 0
    missing = []
    by_field = collections.Counter()
    by_source = collections.Counter()
    examples = []
    for n, r in resolved.items():
        g = live.get(n)
        if g is None:
            missing.append(n)
            continue
        for k, (iv, src) in r["fields"].items():
            if k in SKIP_FIELDS:
                continue
            gv = g.get(k)
            if not se.comparable(gv):
                uncomparable += 1
                continue
            fields += 1
            if not se.same(iv, gv):
                mism += 1
                by_field[k] += 1
                by_source[src.split("@")[0]] += 1
                if len(examples) < 60:
                    examples.append(f"{n}.{k} [{src}]: index={iv!r} game={gv!r}")
    lines = [
        "# bg3-data-mcp in-game ground-truth report", "",
        f"Run {started:%Y-%m-%d %H:%M %Z}. Layers compared: {'+'.join(active)} (entries defined by {a.exclude_layer} skipped).",
        f"{len(resolved)} entries ({min(len(touched), a.sample // 2)} overridden by a mod), {fields} fields, "
        f"{mism} mismatches ({mism / max(fields, 1):.2%}), {uncomparable} functor fields not comparable (game exposes parsed userdata), "
        f"{len(missing)} entries not loaded in game; "
        f"{elapsed:.1f}s of game round-trips.", "",
        "## Mismatches by field", *[f"- {k}: {v}" for k, v in by_field.most_common(25)], "",
        "## Mismatches by layer that set the field", *[f"- {k}: {v}" for k, v in by_source.most_common()], "",
        "## Examples", *[f"- {e}" for e in examples], "",
        "## Not loaded in game", *[f"- {m}" for m in missing[:40]],
    ]
    open(os.path.join(REPO, "tests", "INGAME_REPORT.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines[:12 + len(by_field.most_common(25))]))


if __name__ == "__main__":
    main()
