"""FA001 positive: top-level `if`/`try` blocks are still top level (not a __main__ guard)."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import multiprocessing as mp
import sys


def work():
    print("hi")


if len(sys.argv) > 1:
    try:
        p = mp.Process(target=work)  # expect: FA001
        p.start()
        p.join()
    finally:
        print("done")
