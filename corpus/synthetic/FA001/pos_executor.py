"""FA001 positive: a ProcessPoolExecutor created at top level."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

from concurrent.futures import ProcessPoolExecutor


def square(n):
    return n * n


executor = ProcessPoolExecutor(max_workers=2)  # expect: FA001
print(list(executor.map(square, range(3))))
executor.shutdown()
