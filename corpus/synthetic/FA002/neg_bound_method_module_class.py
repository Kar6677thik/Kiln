"""FA002 negative: a bound method of a module-level class pickles (instance + method name)."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, ANN204, D101, D102, D103
# ruff: noqa: D107, T201

import multiprocessing as mp


class Worker:
    def __init__(self, n):
        self.n = n

    def run_job(self):
        print(self.n)


def main():
    worker = Worker(3)
    p = mp.Process(target=worker.run_job)
    p.start()
    p.join()


if __name__ == "__main__":
    main()
