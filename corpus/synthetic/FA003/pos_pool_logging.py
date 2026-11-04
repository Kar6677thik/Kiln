"""FA003 positive: Pool workers log, but only the parent configured logging."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import logging
import multiprocessing as mp

log = logging.getLogger(__name__)


def square(n):
    log.warning("squaring %d", n)
    return n * n


def main():
    logging.basicConfig(level=logging.DEBUG)
    with mp.Pool(2) as pool:  # expect: FA003
        print(pool.map(square, range(3)))


if __name__ == "__main__":
    main()
