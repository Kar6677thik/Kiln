"""FA003 positive: logging configured directly in the guard; the child calls logging.info."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103

import logging
import multiprocessing as mp


def work():
    logging.info("hello from the child")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    p = mp.Process(target=work)  # expect: FA003
    p.start()
    p.join()
