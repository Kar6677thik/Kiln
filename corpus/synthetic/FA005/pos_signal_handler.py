"""FA005 positive: a signal handler installed at import time is re-installed in every child."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import multiprocessing as mp
import signal


def on_term(signum, frame):
    print("terminating")


signal.signal(signal.SIGTERM, on_term)  # expect: FA005


def work():
    print("child")


if __name__ == "__main__":
    p = mp.Process(target=work)
    p.start()
    p.join()
