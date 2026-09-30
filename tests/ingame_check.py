"""Ground truth: compare the index's resolved stats with what the RUNNING game loaded (via bg3data.se).

  uv run python tests/ingame_check.py [--layers dnd55e] [--sample 300] [--exclude-layer apotheosis] [--only-layer L]

Pick --layers to match what's deployed. Entries defined by --exclude-layer are skipped (e.g. an
outdated deployed pak). With --only-layer every entry that layer defines is checked. Writes
tests/INGAME_REPORT.md. Also available as the MCP tool bg3_ingame_check.
"""
import argparse
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from bg3data import groundtruth, query  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layers", default="dnd55e")
    ap.add_argument("--sample", type=int, default=300)
    ap.add_argument("--exclude-layer", default="apotheosis")
    ap.add_argument("--batch", type=int, default=50)
    ap.add_argument("--only-layer", help="compare only (all) entries this layer defines")
    a = ap.parse_args()
    lines = groundtruth.run(query.Store(refresh=False), [l for l in a.layers.split(",") if l], a.sample, a.exclude_layer,
                            a.batch, a.only_layer, report=os.path.join(REPO, "tests", "INGAME_REPORT.md"))
    print("\n".join(lines[:40]))


if __name__ == "__main__":
    main()
