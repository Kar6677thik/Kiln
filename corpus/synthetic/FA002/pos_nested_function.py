"""FA002 positive (oslo shape): a nested function passed as a Process target."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, ANN202, D103, T201

import multiprocessing as mp


def run_test():
    def target():
        print("in child")

    p = mp.Process(target=target)  # expect: FA002
    p.start()
    p.join()


if __name__ == "__main__":
    run_test()
