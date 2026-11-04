"""FA002 positive: a lambda as the target (lambdas have no importable name)."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import multiprocessing as mp


def main():
    p = mp.Process(target=lambda: print("x"))  # expect: FA002
    p.start()
    p.join()


if __name__ == "__main__":
    main()
