"""FA004 negative: the value is passed to the child as an argument, so it is pickled across."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import multiprocessing as mp

CONFIG = {}


def load():
    CONFIG["db"] = "prod"


def work(config):
    print(config["db"])


if __name__ == "__main__":
    load()
    p = mp.Process(target=work, args=(CONFIG,))
    p.start()
    p.join()
