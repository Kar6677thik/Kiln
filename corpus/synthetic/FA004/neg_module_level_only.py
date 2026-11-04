"""FA004 negative: the global is only assigned at import time, which the child repeats."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import multiprocessing as mp

CONFIG = {"db": "prod"}
CONFIG["port"] = 5432


def work():
    print(CONFIG["db"], CONFIG["port"])


if __name__ == "__main__":
    p = mp.Process(target=work)
    p.start()
    p.join()
