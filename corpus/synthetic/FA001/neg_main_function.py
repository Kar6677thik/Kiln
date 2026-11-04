"""FA001 negative: process creation inside main(), which only the guard calls."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import multiprocessing as mp


def work():
    print("hi")


def main():
    p = mp.Process(target=work)
    p.start()
    p.join()


if __name__ == "__main__":
    main()
