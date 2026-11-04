"""FA001 negative: the guard written the other way round is still a guard."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, SIM300, T201

import multiprocessing as mp


def work():
    print("hi")


if "__main__" == __name__:
    p = mp.Process(target=work)
    p.start()
    p.join()
