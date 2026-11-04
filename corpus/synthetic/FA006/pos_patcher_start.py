"""FA006 positive: patcher.start() before the site, still active at the site."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import multiprocessing as mp
import time
from unittest.mock import patch


def work():
    print(time.time())


def test_frozen_time():
    patcher = patch("time.time", return_value=0.0)
    patcher.start()
    p = mp.Process(target=work)  # expect: FA006
    p.start()
    p.join()
    patcher.stop()
