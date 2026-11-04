"""Launchers: the code that creates worker processes.

WHY THIS MODULE EXISTS
    "How is a worker process created?" has three answers in Python: fork, spawn and
    forkserver (K2). Keeping that code in one package keeps os.fork() out of everything else
    (code-style §7 rule 5).

HOW IT FITS
    launch/base.py              the Launcher protocol (K2)
    launch/fork.py              os.fork() in the master (K1)
    launch/spawn.py             posix_spawn of `python -m kiln.worker` (K2)
    launch/forkserver.py        the master's side of the forkserver (K2)
    launch/forkserver_main.py   the forkserver process itself (K2)
    make_launcher(config) below picks one from `start.method`.

READ FIRST
    docs/concepts/fork.md

MILESTONE
    Introduced in K1. K2 adds the protocol, spawn and forkserver.
"""

from kiln.config import Config
from kiln.launch.base import Launcher


def make_launcher(config: Config) -> Launcher:
    """Pick the launcher for config.start.method.

    Why:
        The only place that maps the setting to a class; the supervisor just asks for one.

    Args:
        config: the configuration.

    Returns:
        A ForkLauncher, SpawnLauncher or ForkserverLauncher.
    """
    # PY: importing inside a function delays loading the module until it's needed.
    if config.start.method == "spawn":
        from kiln.launch.spawn import SpawnLauncher

        return SpawnLauncher()
    if config.start.method == "forkserver":
        from kiln.launch.forkserver import ForkserverLauncher

        return ForkserverLauncher(config)
    from kiln.launch.fork import ForkLauncher

    return ForkLauncher()
