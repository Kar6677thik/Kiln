"""FA006 positive (git-repo shape): patch.object active while the child is created."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D101, D103, T201

import multiprocessing as mp
from unittest import mock


class Config:
    value = 1


def work():
    print(Config.value)


def test_child_sees_patch():
    with mock.patch.object(Config, "value", 5):
        p = mp.Process(target=work)  # expect: FA006
        p.start()
        p.join()
