"""FA007 negative: pass the *file name*, and let the child open the file itself."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, PTH123, S108

import multiprocessing as mp


def work(path):
    with open(path, "a") as handle:
        handle.write("child\n")


def main():
    path = "/tmp/kiln-synthetic.txt"
    p = mp.Process(target=work, args=(path,))
    p.start()
    p.join()


if __name__ == "__main__":
    main()
