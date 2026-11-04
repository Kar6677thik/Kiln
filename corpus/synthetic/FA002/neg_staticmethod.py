"""FA002 negative: a @staticmethod of a module-level class has the qualname Tasks.work."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, ANN205, D101, D102, D103, T201

import multiprocessing as mp


class Tasks:
    @staticmethod
    def work():
        print("work")


def main():
    p = mp.Process(target=Tasks.work)
    p.start()
    p.join()


if __name__ == "__main__":
    main()
