"""FA004 positive: a module-level list grown by .append() in the parent."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import multiprocessing as mp

PLUGINS = []


def register_plugins():
    PLUGINS.append("csv")


def square(n):
    return n * n if "csv" in PLUGINS else n


def main():
    register_plugins()
    with mp.Pool(2) as pool:  # expect: FA004
        print(pool.map(square, range(3)))


if __name__ == "__main__":
    main()
