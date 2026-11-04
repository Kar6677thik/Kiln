"""The fork launcher: create a worker by copying the master with os.fork().

WHY THIS MODULE EXISTS
    os.fork() is the oldest and fastest way to start a worker: the child is an exact copy of
    the master, including the already-imported app, so it starts in milliseconds and shares
    memory pages with the master (copy-on-write, the subject of K3). But the child also
    inherits things it must NOT keep: the master's sockets, its signal handlers, its buffered
    output. This module does the fork and the clean-up, step by step (K1 spec §5.7).

HOW IT FITS
    master/supervisor.py ──fork_worker(...)──▶ (pid, master_end_of_socketpair)
                           child: close inherited fds ──▶ worker/main.run_worker() ──▶ os._exit

READ FIRST
    docs/concepts/fork.md, docs/concepts/fork-and-threads.md,
    docs/concepts/sockets-and-socketpairs.md,
    docs/python/os-module.md

MILESTONE
    Introduced in K1 (as a plain function; K2 extracts a Launcher protocol).
"""

import os
import socket
import sys
import threading
from collections.abc import Sequence

from kiln import logsetup
from kiln.app import App
from kiln.config import Config
from kiln.worker.main import finalize_and_exit, run_worker


class ForkWithThreadsError(AssertionError):
    """The master was about to fork while running a second thread: a Kiln bug.

    Why:
        ADR-006: fork() copies only the calling thread; a lock held by any other thread stays
        locked forever in the child. The master must never have threads, so this is checked
        before *every* fork.
    """


def fork_worker(
    *,
    worker_id: int,
    generation: int,
    config: Config,
    app: App | None,
    close_in_child: Sequence[int],
) -> tuple[int, socket.socket]:
    """Fork one worker; return its pid and the master's end of its channel.

    Why:
        After this returns, the master holds one end of a socketpair and the worker holds the
        other: a private, already-connected line between them (spec/01 §3).

    Args:
        worker_id: the new worker's id.
        generation: its reload generation.
        config: the configuration (the child gets its own copy by fork).
        app: the preloaded app, or None if the worker should import it.
        close_in_child: fds the child must close: the listener, client connections, other
            workers' channels and the signal pipe (spec/04 §6 rule 1).

    Returns:
        (child pid, master's socket).

    Raises:
        ForkWithThreadsError: the master has more than one thread.
    """
    # INVARIANT: the master is single-threaded (ADR-006, docs/concepts/fork-and-threads.md).
    if threading.active_count() != 1:
        raise ForkWithThreadsError(
            f"master has {threading.active_count()} threads at fork time; "
            "see docs/concepts/fork-and-threads.md"
        )
    # Step 1: the private channel. Each end is a full socket; one per process after the fork.
    parent_sock, child_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    # Step 2. GOTCHA: anything still sitting in a buffer is *copied* into the child and then
    # printed by both processes. Flush first.
    sys.stdout.flush()
    sys.stderr.flush()
    logsetup.flush_all()
    # Step 3. CONCEPT(fork): after this line there are TWO processes running this same code.
    # The parent gets the child's pid; the child gets 0.
    # SYSCALL: os.fork → fork(2) (clone(2) on Linux); see 'man 2 fork'.
    pid = os.fork()
    if pid == 0:
        _child(child_sock, parent_sock, worker_id, generation, config, app, close_in_child)
    # Step 5 (parent): close the child's end, which now belongs to the child process.
    child_sock.close()
    return pid, parent_sock


def _child(
    child_sock: socket.socket,
    parent_sock: socket.socket,
    worker_id: int,
    generation: int,
    config: Config,
    app: App | None,
    close_in_child: Sequence[int],
) -> None:
    """The child side of the fork. Never returns: it always ends in os._exit (ADR-028).

    Why:
        If this function ever returned, the child would continue running the *master's* code
        right after os.fork(): two masters. The try/finally makes os._exit unavoidable.

    Args:
        child_sock: the worker's end of the channel.
        parent_sock: the master's end (the child closes its copy).
        worker_id: the worker id.
        generation: the reload generation.
        config: the configuration.
        app: the preloaded app or None.
        close_in_child: inherited fds to close.
    """
    code = 1
    try:
        # Step 4 (child): close every inherited fd we must not keep (spec/04 §6 rule 1).
        # GOTCHA: if a worker kept the master's listening socket open, the port would stay
        # busy after the master died, and a worker could even accept() a client by accident.
        parent_sock.close()
        for fd in close_in_child:
            try:
                os.close(fd)
            except OSError:
                pass  # WHY: already closed (EBADF) is fine
        code = run_worker(
            channel=child_sock, worker_id=worker_id, generation=generation, config=config, app=app
        )
    except BaseException as error:  # noqa: BLE001  # WHY: the child's outermost boundary (code-style §4)
        print(f"kiln worker {worker_id}: fatal {type(error).__name__}: {error}", file=sys.stderr)  # noqa: T201  # WHY: logging may be broken here
        code = 1
    finally:
        finalize_and_exit(code)
