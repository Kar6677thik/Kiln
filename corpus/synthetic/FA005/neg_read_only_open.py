"""FA005 negative: opening a file for *reading* at import time isn't a repeated side effect."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, PTH123, T201

import multiprocessing as mp

with open(__file__) as handle:
    LINES = handle.read().count("\n")


def work():
    print(LINES)


if __name__ == "__main__":
    p = mp.Process(target=work)
    p.start()
    p.join()
