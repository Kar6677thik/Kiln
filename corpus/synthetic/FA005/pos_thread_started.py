"""FA005 positive: a background thread started at import time."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import multiprocessing as mp
import threading
import time


def heartbeat():
    time.sleep(0.1)


threading.Thread(target=heartbeat, daemon=True).start()  # expect: FA005


def work():
    print("child")


if __name__ == "__main__":
    p = mp.Process(target=work)
    p.start()
    p.join()
