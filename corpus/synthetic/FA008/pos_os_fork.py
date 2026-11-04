"""FA008 positive: calling os.fork() directly."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103

import os


def main():
    pid = os.fork()  # expect: FA008
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)


if __name__ == "__main__":
    main()
