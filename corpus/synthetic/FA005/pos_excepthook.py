"""FA005 positive: replacing sys.excepthook at import time."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import multiprocessing as mp
import sys


def hook(kind, value, tb):
    print("uncaught:", value)


sys.excepthook = hook  # expect: FA005


def work():
    print("child")


if __name__ == "__main__":
    p = mp.Process(target=work)
    p.start()
    p.join()
