"""FA007 negative: ints, strings, lists and dicts pickle fine."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import multiprocessing as mp


def work(n, name, items, options):
    print(n, name, items, options)


def main():
    p = mp.Process(target=work, args=(1, "a", [1, 2], {"k": "v"}))
    p.start()
    p.join()


if __name__ == "__main__":
    main()
