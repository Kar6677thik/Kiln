"""FA004 negative: a global is changed in the parent, but the target never reads it."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import multiprocessing as mp

CONFIG = {}


def load():
    CONFIG["db"] = "prod"


def work(n):
    print(n * 2)


if __name__ == "__main__":
    load()
    p = mp.Process(target=work, args=(21,))
    p.start()
    p.join()
