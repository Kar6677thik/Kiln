"""FA006 positive: a test decorated with @mock.patch starts a process."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, PTH109, T201

import multiprocessing as mp
import os
from unittest import mock


def work():
    print(os.getcwd())


@mock.patch("os.getcwd", return_value="/fake")
def test_cwd(fake_getcwd):
    p = mp.Process(target=work)  # expect: FA006
    p.start()
    p.join()
