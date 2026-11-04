"""FA007 positive: a lambda passed inline as an argument."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import multiprocessing as mp


def work(callback):
    callback()


def main():
    p = mp.Process(target=work, args=(lambda: print("cb"),))  # expect: FA007
    p.start()
    p.join()


if __name__ == "__main__":
    main()
