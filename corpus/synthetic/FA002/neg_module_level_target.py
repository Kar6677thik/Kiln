"""FA002 negative: a module-level function pickles fine (by its name)."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import multiprocessing as mp


def target():
    print("in child")


def main():
    p = mp.Process(target=target)
    p.start()
    p.join()


if __name__ == "__main__":
    main()
