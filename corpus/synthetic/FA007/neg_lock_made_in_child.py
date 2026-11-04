"""FA007 negative: the threading.Lock is created inside the child, not sent to it."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import multiprocessing as mp
import threading


def work():
    lock = threading.Lock()
    with lock:
        print("locked")


def main():
    p = mp.Process(target=work)
    p.start()
    p.join()


if __name__ == "__main__":
    main()
