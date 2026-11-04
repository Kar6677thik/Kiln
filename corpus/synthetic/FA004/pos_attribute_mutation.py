"""FA004 positive: an attribute of a module-level object is changed before the child starts."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import multiprocessing as mp
import types

SETTINGS = types.SimpleNamespace(debug=False)


def enable_debug():
    SETTINGS.debug = True


def work():
    print("debug:", SETTINGS.debug)


if __name__ == "__main__":
    enable_debug()
    p = mp.Process(target=work)  # expect: FA004
    p.start()
    p.join()
