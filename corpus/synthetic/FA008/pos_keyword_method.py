"""FA008 positive: the method passed by keyword."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103

import multiprocessing


def main():
    multiprocessing.set_start_method(method="fork", force=True)  # expect: FA008


if __name__ == "__main__":
    main()
