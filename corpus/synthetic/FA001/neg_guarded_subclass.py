"""FA001 negative: a Process subclass, started under the guard."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D101, D102, T201

import multiprocessing as mp


class Daemon(mp.Process):
    def run(self):
        print("serving")


if __name__ == "__main__":
    daemon = Daemon()
    daemon.start()
    daemon.join()
