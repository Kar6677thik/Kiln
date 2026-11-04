"""The fork launcher: create a worker by copying the master with os.fork().

WHY THIS MODULE EXISTS
    os.fork() is the oldest and fastest way to start a worker: the child is an exact copy of
    the master, including the already-imported app, so it starts in milliseconds and shares
    memory pages with the master (copy-on-write, the subject of K3). But the child also
    inherits things it must NOT keep: the master's sockets, its signal handlers, its buffered
    output. This module does the fork and the clean-up, step by step (K1 spec §5.7).

HOW IT FITS
    master/supervisor.py ──ForkLauncher.launch(spec)──▶ fork_child(spec) ──▶ pid
                           child: close inherited fds ──▶ worker/main.run_worker() ──▶ os._exit

READ FIRST
    docs/concepts/fork.md, docs/concepts/fork-and-threads.md,
    docs/concepts/sockets-and-socketpairs.md,
    docs/python/os-module.md

MILESTONE
    Introduced in K1; K2 put it behind the Launcher protocol (launch/base.py) and shares
    fork_child() with the forkserver.
"""

import os
import socket
import sys
import threading

from kiln import logsetup
from kiln.launch.base import Launched, LauncherEvent, Pending, WorkerLaunchSpec
from kiln.worker.main import finalize_and_exit, run_worker


class ForkWithThreadsError(AssertionError):
    """The master was about to fork while running a second thread: a Kiln bug.

    Why:
        ADR-006: fork() copies only the calling thread; a lock held by any other thread stays
        locked forever in the child. The master must never have threads, so this is checked
        before *every* fork.
    """


def fork_child(spec: WorkerLaunchSpec) -> int:
    """Fork one worker that will use `spec.channel_child` as its channel; return its pid.

    Why:
        Shared by the fork launcher (in the master) and the forkserver (in the server process):
        both create workers the same way, with the same clean-up in the child.

    Args:
        spec: what to start; close_in_child lists the inherited fds the child must close.

    Returns:
        The child's pid (in the parent; the child never returns).

    Raises:
        ForkWithThreadsError: the calling process has more than one thread.
    """
    # INVARIANT: the forking process is single-threaded (ADR-006,
    # docs/concepts/fork-and-threads.md).
    if threading.active_count() != 1:
        raise ForkWithThreadsError(
            f"{threading.active_count()} threads at fork time; "
            "see docs/concepts/fork-and-threads.md"
        )
    # GOTCHA: anything still sitting in a buffer is *copied* into the child and then printed by
    # both processes. Flush first.
    sys.stdout.flush()
    sys.stderr.flush()
    logsetup.flush_all()
    # CONCEPT(fork): after this line there are TWO processes running this same code.
    # The parent gets the child's pid; the child gets 0.
    # SYSCALL: os.fork → fork(2) (clone(2) on Linux); see 'man 2 fork'.
    pid = os.fork()
    if pid == 0:
        _child(spec)
    return pid


class ForkLauncher:
    """Start workers by forking the master (the K1 method, now behind the Launcher protocol).

    Why:
        The fastest start method: the child is a copy of the master, preloaded app included,
        sharing memory pages through copy-on-write (K3).
    """

    name = "fork"
    reaps_children = True
    pid = 0  # WHY: no helper process

    def start(self) -> None:
        """Nothing to prepare."""

    def launch(self, spec: WorkerLaunchSpec) -> Launched | Pending:
        """Fork one worker.

        Args:
            spec: what to start.

        Returns:
            Launched with the pid.
        """
        return Launched(fork_child(spec))

    def selectable(self) -> socket.socket | None:
        """Fork has no socket of its own.

        Returns:
            None.
        """
        return None

    def on_readable(self) -> list[LauncherEvent]:
        """Never called.

        Returns:
            An empty list.
        """
        return []

    def owns_pid(self, pid: int) -> bool:
        """Fork has no helper process.

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


def _child(spec: WorkerLaunchSpec) -> None:
    """The child side of the fork. Never returns: it always ends in os._exit (ADR-028).

    Why:
        If this function ever returned, the child would continue running the *parent's* code
        right after os.fork(): two masters. The try/finally makes os._exit unavoidable.

    Args:
        spec: the launch spec (channel, ids, config, app, fds to close).
    """
    code = 1
    try:
        # Step 4 (child): close every inherited fd we must not keep (spec/04 §6 rule 1).
        # GOTCHA: if a worker kept the master's listening socket open, the port would stay
        # busy after the master died, and a worker could even accept() a client by accident.
        for fd in spec.close_in_child:
            try:
                os.close(fd)
            except OSError:
                pass  # WHY: already closed (EBADF) is fine
        code = run_worker(
            channel=spec.channel_child,
            worker_id=spec.worker_id,
            generation=spec.generation,
            config=spec.config,
            app=spec.app,
        )
    except BaseException as error:  # noqa: BLE001  # WHY: the child's outermost boundary (code-style §4)
        # WHY: logging may be broken this early, so write straight to stderr.
        sys.stderr.write(f"kiln worker {spec.worker_id}: fatal {type(error).__name__}: {error}\n")
        code = 1
    finally:
        finalize_and_exit(code)
