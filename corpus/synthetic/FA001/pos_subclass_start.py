"""FA001 positive (krb5 shape): a Process subclass started at top level; the site is .start()."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D101, D102, T201

import multiprocessing as mp


class Daemon(mp.Process):
    def run(self):
        print("serving")


daemon = Daemon()
daemon.start()  # expect: FA001
daemon.join()
