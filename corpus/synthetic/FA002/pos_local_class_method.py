"""FA002 positive: a method of a class defined inside a function."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, ANN202, D103, T201

import multiprocessing as mp


def main():
    class Job:
        def go(self):
            print("going")

    job = Job()
    p = mp.Process(target=job.go)  # expect: FA002
    p.start()
    p.join()


if __name__ == "__main__":
    main()
