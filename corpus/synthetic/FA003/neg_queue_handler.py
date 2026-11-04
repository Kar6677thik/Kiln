"""FA003 negative: the child sends records to the parent through a QueueHandler."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103

import logging
import logging.handlers
import multiprocessing as mp


def work(queue):
    logging.getLogger().addHandler(logging.handlers.QueueHandler(queue))
    logging.warning("via the queue")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    queue = mp.Queue()
    p = mp.Process(target=work, args=(queue,))
    p.start()
    p.join()
