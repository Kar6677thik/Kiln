"""FA003 negative: logging is configured in the parent, but the child never logs."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import logging
import multiprocessing as mp


def work():
    print("plain print")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.info("parent only")
    p = mp.Process(target=work)
    p.start()
    p.join()
