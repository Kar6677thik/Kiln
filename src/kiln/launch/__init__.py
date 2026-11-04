"""Launchers: the code that creates worker processes.

WHY THIS MODULE EXISTS
    "How is a worker process created?" has three answers in Python: fork, spawn and
    forkserver (K2). Keeping that code in one package keeps os.fork() out of everything else
    (code-style §7 rule 5).

HOW IT FITS
    launch/fork.py  K1: os.fork() in the master. K2 adds spawn.py, forkserver.py and a
                    Launcher protocol.

READ FIRST
    docs/concepts/fork.md

MILESTONE
    Introduced in K1.
"""
