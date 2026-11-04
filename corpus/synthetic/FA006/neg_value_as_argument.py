"""FA006 negative: the value is passed as an argument instead of patched."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import multiprocessing as mp


def work(value):
    print(value)


def test_value_passed():
    p = mp.Process(target=work, args=(5,))
    p.start()
    p.join()
