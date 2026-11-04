"""FA006 negative: the patch is applied inside the target, i.e. in the child itself."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D101, D103, T201

import multiprocessing as mp
from unittest import mock


class Config:
    value = 1


def work():
    with mock.patch.object(Config, "value", 5):
        print(Config.value)


def test_child_patches_itself():
    p = mp.Process(target=work)
    p.start()
    p.join()
