"""FA008 negative: other os functions are not os.fork."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import os


def main():
    print(os.getpid(), os.getppid())


if __name__ == "__main__":
    main()
