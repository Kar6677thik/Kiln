"""FA002 negative: functools.partial of a module-level function is picklable."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import functools
import multiprocessing as mp


def work(n):
    print(n)


def main():
    p = mp.Process(target=functools.partial(work, 1))
    p.start()
    p.join()


if __name__ == "__main__":
    main()
