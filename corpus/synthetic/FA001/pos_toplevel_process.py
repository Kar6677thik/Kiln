"""FA001 positive: a Process created and started at module top level, with no guard."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import multiprocessing as mp


def work():
    print("hi")


p = mp.Process(target=work)  # expect: FA001
p.start()
p.join()
