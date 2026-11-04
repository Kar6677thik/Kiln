"""FA001 negative: the same three lines under the __main__ guard."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import multiprocessing as mp


def work():
    print("hi")


if __name__ == "__main__":
    p = mp.Process(target=work)
    p.start()
    p.join()
