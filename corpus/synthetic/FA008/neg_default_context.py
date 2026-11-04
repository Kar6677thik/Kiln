"""FA008 negative: get_context() with no argument uses the platform default."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103

import multiprocessing as mp


def main():
    return mp.get_context()


if __name__ == "__main__":
    main()
