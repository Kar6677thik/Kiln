"""FA005 positive: the main script registers an atexit handler at import time."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import atexit
import multiprocessing as mp


def goodbye():
    print("re-run this test!")


atexit.register(goodbye)  # expect: FA005


def work():
    print("child")


if __name__ == "__main__":
    p = mp.Process(target=work)
    p.start()
    p.join()
