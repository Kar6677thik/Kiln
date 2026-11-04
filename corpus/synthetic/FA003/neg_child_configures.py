"""FA003 negative: the target configures logging itself (via setup_logging)."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103

import logging
import multiprocessing as mp

log = logging.getLogger(__name__)


def setup_logging():
    logging.basicConfig(level=logging.INFO)


def work():
    setup_logging()
    log.info("configured in the child too")


if __name__ == "__main__":
    setup_logging()
    p = mp.Process(target=work)
    p.start()
    p.join()
