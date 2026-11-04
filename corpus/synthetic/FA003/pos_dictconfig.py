"""FA003 positive: logging.config.dictConfig in a setup function the guard calls."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103

import logging
import logging.config
import multiprocessing as mp


def setup():
    logging.config.dictConfig({"version": 1, "root": {"level": "INFO"}})


def work():
    logging.error("boom")


def main():
    setup()
    p = mp.Process(target=work)  # expect: FA003
    p.start()
    p.join()


if __name__ == "__main__":
    main()
