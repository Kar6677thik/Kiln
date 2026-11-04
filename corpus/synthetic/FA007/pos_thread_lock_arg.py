"""FA007 positive: a threading.Lock passed to a Process (cannot pickle '_thread.lock')."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import multiprocessing as mp
import threading


def work(lock):
    with lock:
        print("locked")


def main():
    lock = threading.Lock()
    p = mp.Process(target=work, args=(lock,))  # expect: FA007
    p.start()
    p.join()


if __name__ == "__main__":
    main()
