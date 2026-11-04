"""FA003 positive (pyfeeds shape): logging configured in main(); the child logs via a logger."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103

import logging
import multiprocessing as mp

log = logging.getLogger(__name__)


def work(n):
    log.info("job %d", n)


def main():
    logging.basicConfig(level=logging.INFO)
    p = mp.Process(target=work, args=(1,))  # expect: FA003
    p.start()
    p.join()


if __name__ == "__main__":
    main()
