"""FA005 negative: the side effect is inside a function the guard calls."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import atexit
import multiprocessing as mp


def goodbye():
    print("bye")


def setup():
    atexit.register(goodbye)


def work():
    print("child")


if __name__ == "__main__":
    setup()
    p = mp.Process(target=work)
    p.start()
    p.join()
