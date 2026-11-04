"""FA004 positive: `global MODE` rebinds a module global before the child starts."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import multiprocessing as mp

MODE = "test"


def configure(mode):
    global MODE
    MODE = mode


def work():
    print("mode is", MODE)


def main():
    configure("production")
    p = mp.Process(target=work)  # expect: FA004
    p.start()
    p.join()


if __name__ == "__main__":
    main()
