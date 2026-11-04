"""FA002 positive: functools.partial wrapping a nested function."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, ANN202, D103, T201

import functools
import multiprocessing as mp


def main():
    def inner(n):
        print(n)

    p = mp.Process(target=functools.partial(inner, 1))  # expect: FA002
    p.start()
    p.join()


if __name__ == "__main__":
    main()
