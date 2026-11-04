"""FA005 negative: the side effect is under the __main__ guard (children skip it)."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import multiprocessing as mp
import signal


def on_term(signum, frame):
    print("terminating")


def work():
    print("child")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, on_term)
    p = mp.Process(target=work)
    p.start()
    p.join()
