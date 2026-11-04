"""FA005 negative: import-time side effects, but no scanned module starting processes uses it."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import atexit


def goodbye():
    print("bye")


atexit.register(goodbye)
