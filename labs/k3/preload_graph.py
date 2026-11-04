"""Build a K3 preload graph and print what it is made of.

WHY THIS MODULE EXISTS
    "300 MiB of Python objects" is abstract. This script builds the same graph the K3 lab
    preloads (kiln.labs.preload.build_graph) and prints its composition: how many dicts,
    lists, strs, ints and floats, what tracemalloc measured, how long it took, and how much
    the process's memory grew. The code tour runs it.

HOW IT FITS
    ./scripts/dev.sh run labs/k3/preload_graph.py [--mb 300] [--composition mortal]
        ──▶ kiln.labs.preload.build_graph ──▶ describe() ──▶ JSON on stdout

READ FIRST
    docs/concepts/copy-on-write.md, docs/python/tracemalloc-basics.md

MILESTONE
    Introduced in K3.
"""

import argparse
import json

from kiln.labs import preload
from kiln.memory.procfs import read_smaps_rollup


def main() -> None:
    """Build one graph and print its description.

    Why:
        The tour and the lab report quote these numbers, so they come from a real run.
    """
    parser = argparse.ArgumentParser(description="build and describe a K3 preload graph")
    parser.add_argument("--mb", type=int, default=300)
    parser.add_argument("--composition", choices=("mortal", "immortal-heavy"), default="mortal")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    before = read_smaps_rollup("self")
    graph = preload.build_graph(args.mb, seed=args.seed, composition=args.composition)
    after = read_smaps_rollup("self")
    info = preload.describe(graph)
    info["rss_growth_mib"] = round((after.rss - before.rss) / preload.MIB, 1)
    info["private_dirty_growth_mib"] = round(
        (after.private_dirty - before.private_dirty) / preload.MIB, 1
    )
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
