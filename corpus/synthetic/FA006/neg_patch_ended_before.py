"""FA006 negative: the `with patch` block ends before the child is created."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D101, D103, T201

import multiprocessing as mp
from unittest import mock


class Config:
    value = 1


def work():
    print(Config.value)


def test_patch_scope():
    with mock.patch.object(Config, "value", 5):
        print(Config.value)
    p = mp.Process(target=work)
    p.start()
    p.join()
