"""The Launcher protocol: what every start method (fork, spawn, forkserver) must provide.

WHY THIS MODULE EXISTS
    "How is a worker process created?" has three answers (docs/concepts/start-methods.md), and
    each behaves differently: fork copies the master, spawn starts a fresh interpreter, and
    forkserver asks a clean helper process to fork. The supervisor shouldn't care which one is
    in use. This module describes the common interface, so the supervisor talks to "a launcher"
    and the start method becomes a config choice (`start.method`,
    docs/milestones/K2-start-methods-forkcheck.md §5.1).

HOW IT FITS
    master/supervisor.py ──launcher.launch(WorkerLaunchSpec)──▶ Launched(pid) | Pending
    master/arbiter.py ──launcher.selectable() readable──▶ launcher.on_readable() ──▶ events
        Spawned(worker_id, pid)  ChildExit(pid, status)   (forkserver only)
    Implementations: launch/fork.py, launch/spawn.py, launch/forkserver.py

READ FIRST
    docs/concepts/start-methods.md, docs/python/typing-protocol.md

MILESTONE
    Introduced in K2.
"""

import socket
from dataclasses import dataclass
from typing import Protocol

from kiln.app import App
from kiln.config import Config


@dataclass(frozen=True, slots=True)
class WorkerLaunchSpec:
    """Everything a launcher needs to start one worker.

    Why:
        One small, read-only bundle instead of a long argument list, the same for every method.
    """

    worker_id: int
    generation: int
    config: Config
    channel_child: socket.socket  # the worker's end of its socketpair with the master
    close_in_child: tuple[int, ...]  # fds a *forked* child must close (spec/04 §6 rule 1)
    app: App | None  # the app preloaded in the master (fork only), else None


@dataclass(frozen=True, slots=True)
class Launched:
    """The worker exists and its pid is known right away (fork, spawn).

    Why:
        The supervisor can track it by pid immediately.
    """

    pid: int


@dataclass(frozen=True, slots=True)
class Pending:
    """The worker is being created elsewhere; its pid arrives later in a Spawned event.

    Why:
        With forkserver, the *server* forks, so the master learns the pid only when the server
        replies `spawned` (spec/02 §4.3). The master must not wait for that reply (ADR-006).
    """


@dataclass(frozen=True, slots=True)
class Spawned:
    """The forkserver created worker `worker_id` with process id `pid`.

    Why:
        Completes a Pending launch.
    """

    worker_id: int
    pid: int


@dataclass(frozen=True, slots=True)
class ChildExit:
    """The forkserver reaped one of its children: here is the raw wait status.

    Why:
        Only a process's parent can waitpid it; the forkserver is the parent of its workers, so
        it reports their deaths and the master classifies them exactly as if it had reaped them.
    """

    pid: int
    wait_status: int


# PY: a union type alias: "one of these two classes" (docs/python/type-hints.md).
LauncherEvent = Spawned | ChildExit


class Launcher(Protocol):
    """What the supervisor needs from any start method.

    Why:
        A Protocol states the required methods without forcing inheritance; mypy checks that
        ForkLauncher, SpawnLauncher and ForkserverLauncher all match
        (docs/python/typing-protocol.md).
    """

    name: str
    # True: the master reaps the workers itself (fork, spawn); False: the forkserver does.
    reaps_children: bool
    # The launcher's helper process (the forkserver), or 0 if it has none.
    pid: int

    def start(self) -> None:
        """Prepare the launcher (forkserver: start the server); no-op for fork and spawn."""
        ...

    def launch(self, spec: WorkerLaunchSpec) -> Launched | Pending:
        """Start one worker.

        Args:
            spec: what to start.

        Returns:
            Launched with the pid, or Pending (forkserver).
        """
        ...

    def selectable(self) -> socket.socket | None:
        """A socket the master's selector should watch for this launcher, if any.

        Returns:
            The forkserver control socket, or None.
        """
        ...

    def on_readable(self) -> list[LauncherEvent]:
        """Read whatever the launcher's socket has and turn it into events.

        Returns:
            Spawned / ChildExit events.
        """
        ...

    def owns_pid(self, pid: int) -> bool:
        """Whether a reaped pid is the launcher's own helper process (the forkserver).

        Args:
            pid: a pid returned by waitpid.

        Returns:
            True for the forkserver process.
        """
        ...

    def extra_fds(self) -> list[int]:
        """Fds a forked child must close in addition to the master's own.

        Returns:
            The fd numbers.
        """
        ...

    def stop(self) -> None:
        """Shut the launcher down (forkserver: tell the server to exit)."""
        ...
