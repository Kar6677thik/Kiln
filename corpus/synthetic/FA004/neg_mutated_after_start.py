"""FA004 negative: the mutation happens after the child has started (not "before the site")."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import multiprocessing as mp

COUNTS = {}


def record():
    COUNTS["runs"] = 1


def work():
    print(COUNTS)


if __name__ == "__main__":
    p = mp.Process(target=work)
    p.start()
    p.join()
    record()
