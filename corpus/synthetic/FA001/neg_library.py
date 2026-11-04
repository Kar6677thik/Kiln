"""FA001 negative: a library that only *defines* functions and classes that start processes."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D101, D102, D103, T201

import multiprocessing as mp


def work():
    print("hi")


def start_worker():
    p = mp.Process(target=work)
    p.start()
    return p


class Runner:
    def launch(self):
        with mp.Pool(2) as pool:
            return pool.map(abs, [-1, -2])
