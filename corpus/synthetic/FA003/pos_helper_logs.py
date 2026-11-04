"""FA003 positive: the target calls a helper that logs (followed one call deep)."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103

import logging
import multiprocessing as mp

log = logging.getLogger(__name__)


def report(n):
    log.info("result %d", n)


def work(n):
    report(n * 2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    p = mp.Process(target=work, args=(4,))  # expect: FA003
    p.start()
    p.join()
