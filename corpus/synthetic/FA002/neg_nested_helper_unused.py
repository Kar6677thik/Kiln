"""FA002 negative: a nested helper exists, but the target is a module-level function."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, ANN202, D103, T201

import multiprocessing as mp


def work():
    print("work")


def main():
    def describe():
        return "starting"

    print(describe())
    p = mp.Process(target=work)
    p.start()
    p.join()


if __name__ == "__main__":
    main()
