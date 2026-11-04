"""FA007 positive: a generator object (from a generator function) passed to the child."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import multiprocessing as mp


def numbers():
    yield 1
    yield 2


def work(items):
    print(list(items))


def main():
    items = numbers()
    p = mp.Process(target=work, args=(items,))  # expect: FA007
    p.start()
    p.join()


if __name__ == "__main__":
    main()
