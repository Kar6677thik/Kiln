"""FA007 positive: an open file object (a `with open(...) as f` target) passed to a Process."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, PTH123, S108

import multiprocessing as mp


def work(handle):
    handle.write("child\n")


def main():
    with open("/tmp/kiln-synthetic.txt", "w") as handle:
        p = mp.Process(target=work, args=(handle,))  # expect: FA007
        p.start()
        p.join()


if __name__ == "__main__":
    main()
