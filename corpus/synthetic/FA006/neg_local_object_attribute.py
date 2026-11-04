"""FA006 negative: setting an attribute on a local object (not an imported module or class)."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import multiprocessing as mp
import types


def work(options):
    print(options.verbose)


def main():
    options = types.SimpleNamespace()
    options.verbose = True
    p = mp.Process(target=work, args=(options,))
    p.start()
    p.join()


if __name__ == "__main__":
    main()
