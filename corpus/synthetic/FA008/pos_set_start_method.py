"""FA008 positive: set_start_method("fork") under the guard."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import multiprocessing as mp


def work():
    print("child")


if __name__ == "__main__":
    mp.set_start_method("fork")  # expect: FA008
    p = mp.Process(target=work)
    p.start()
    p.join()
