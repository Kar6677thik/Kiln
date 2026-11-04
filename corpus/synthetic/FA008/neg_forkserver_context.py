"""FA008 negative: get_context("forkserver")."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103

import multiprocessing as mp


def main():
    return mp.get_context("forkserver")


if __name__ == "__main__":
    main()
