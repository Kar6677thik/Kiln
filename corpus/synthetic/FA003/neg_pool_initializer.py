"""FA003 negative: Pool(initializer=setup_logging) configures logging in every worker."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import logging
import multiprocessing as mp

log = logging.getLogger(__name__)


def setup_logging():
    logging.basicConfig(level=logging.INFO)


def square(n):
    log.info("squaring %d", n)
    return n * n


if __name__ == "__main__":
    setup_logging()
    with mp.Pool(2, initializer=setup_logging) as pool:
        print(pool.map(square, range(3)))
