"""The spawn launcher: start each worker as a brand-new Python interpreter.

WHY THIS MODULE EXISTS
    spawn is the opposite of fork: nothing is copied. The worker is a fresh `python -m
    kiln.worker` process that imports the app itself. It is the slowest start method and shares
    no memory, but it's the only one that works on every OS, and it exposes every hidden
    "fork assumption" in code (docs/concepts/start-methods.md). The only thing the new process
    inherits from the master is its end of the private channel, passed as a file descriptor.

HOW IT FITS
    supervisor ──SpawnLauncher.launch(spec)──▶ os.posix_spawn(python -m kiln.worker --fd N ...)
        ──▶ worker/__main__.py ──▶ worker/main.run_worker(app=None) ──▶ sys.exit(code)
    The master reaps spawned workers itself with waitpid (they are its children).

READ FIRST
    docs/concepts/start-methods.md, docs/concepts/fd-passing.md (inheritance, PEP 446),
    docs/python/os-module.md

MILESTONE
    Introduced in K2.
"""

import os
import socket
import sys
from pathlib import Path

from kiln.config import Config
from kiln.launch.base import Launched, LauncherEvent, Pending, WorkerLaunchSpec


def worker_command(spec: WorkerLaunchSpec, fd: int) -> list[str]:
    """Build the argv of a spawned worker.

    Why:
        The worker learns everything from its command line: which fd is its channel, who it is,
        and the full config as JSON (Config.to_json).

    Args:
        spec: the launch spec.
        fd: the channel fd number the child will see.

    Returns:
        The argument list, starting with the interpreter.
    """
    return [
        sys.executable,
        "-m",
        "kiln.worker",
        "--fd",
        str(fd),
        "--worker-id",
        str(spec.worker_id),
        "--generation",
        str(spec.generation),
        "--config-json",
        spec.config.to_json(),
    ]


def child_environment_for(config: Config) -> dict[str, str]:
    """The environment for a spawned process: ours, plus app.pythonpath and the cwd on PYTHONPATH.

    Why:
        A fresh interpreter only finds the app if its folder is importable. The master's
        sys.path changes (like adding the cwd in `kiln serve`) don't travel to a new process;
        environment variables do. Used by spawn workers and by the forkserver.

    Args:
        config: the configuration.

    Returns:
        The environment dict.
    """
    env = dict(os.environ)
    extra = [*config.app.pythonpath, str(Path.cwd())]
    if env.get("PYTHONPATH"):
        extra.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(extra)
    return env


class SpawnLauncher:
    """Start workers with os.posix_spawn.

    Why:
        posix_spawn (not subprocess.Popen): Popen keeps its own records of its children and may
        call waitpid on them itself, racing with the master's waitpid(-1) reap loop. posix_spawn
        just returns a pid and leaves reaping to us.
    """

    name = "spawn"
    reaps_children = True
    pid = 0  # WHY: no helper process

    def start(self) -> None:
        """Nothing to prepare."""

    def launch(self, spec: WorkerLaunchSpec) -> Launched | Pending:
        """Spawn one worker process.

        Why:
            Every fd Python creates is non-inheritable by default (PEP 446), so the new program
            starts with only stdin/stdout/stderr, plus the one fd we mark inheritable here.

        Args:
            spec: what to start.

        Returns:
            Launched with the new pid.
        """
        fd = spec.channel_child.fileno()
        # CONCEPT(fd-passing): mark exactly one fd as "survives exec".
        os.set_inheritable(fd, True)
        # SYSCALL: os.posix_spawn → posix_spawn(3): create a process running a new program.
        pid = os.posix_spawn(
            sys.executable, worker_command(spec, fd), child_environment_for(spec.config)
        )
        return Launched(pid)

    def selectable(self) -> socket.socket | None:
        """Spawn has no socket of its own.

        Returns:
            None.
        """
        return None

    def on_readable(self) -> list[LauncherEvent]:
        """Never called (no selectable).

        Returns:
            An empty list.
        """
        return []

    def owns_pid(self, pid: int) -> bool:
        """Spawn has no helper process.

        Args:
            pid: a reaped pid.

        Returns:
            False.
        """
        return False

    def extra_fds(self) -> list[int]:
        """No extra fds.

        Returns:
            An empty list.
        """
        return []

    def stop(self) -> None:
        """Nothing to stop."""
