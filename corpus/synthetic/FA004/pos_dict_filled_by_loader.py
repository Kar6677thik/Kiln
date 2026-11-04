"""FA004 positive (the spec example): CONFIG is filled in the parent, read by the child."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import multiprocessing as mp

CONFIG = {}


def load():
    CONFIG["db"] = "prod"


def work():
    print(CONFIG["db"])


if __name__ == "__main__":
    load()
    p = mp.Process(target=work)  # expect: FA004
    p.start()
    p.join()
