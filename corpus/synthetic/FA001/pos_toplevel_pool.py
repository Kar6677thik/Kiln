"""FA001 positive: a Pool created at top level (the `with` line is the creation site)."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import multiprocessing as mp


def square(n):
    return n * n


with mp.Pool(2) as pool:  # expect: FA001
    print(pool.map(square, range(3)))
