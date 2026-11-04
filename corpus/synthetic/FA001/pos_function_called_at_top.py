"""FA001 positive: a function that starts a process, called at top level (one level deep)."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import multiprocessing as mp


def work():
    print("hi")


def main():
    p = mp.Process(target=work)
    p.start()
    p.join()


main()  # expect: FA001
