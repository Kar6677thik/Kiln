"""The master's record of each worker, and a table to look workers up quickly.

WHY THIS MODULE EXISTS
    The master must know, for every worker: its process id, its state (booting, ready,
    retiring), how many jobs it is running and which, when it last sent a heartbeat, and
    whether the master itself sent it a kill signal (that's how "killed by us" is told apart
    from "killed by the OOM killer"). docs/spec/04-supervisor.md §1–2 define all of it.

HOW IT FITS
    supervisor.py ──creates/updates──▶ WorkerHandle (one per worker process)
    dispatcher.py ──ready_with_capacity()──▶ which worker gets the next job
    arbiter.py ──by_fd / by_pid──▶ which worker a socket event or a reaped pid belongs to

READ FIRST
    docs/spec/04-supervisor.md §1–2, docs/concepts/state-machines.md

MILESTONE
    Introduced in K1.
"""

import signal
from dataclasses import dataclass, field
from enum import StrEnum

from kiln.master.connections import Connection


class WorkerState(StrEnum):
    """The life of a worker (spec/04 §1).

    Why:
        Only READY workers get jobs; RETIRING ones finish what they have and leave.
    """

    BOOTING = "BOOTING"
    READY = "READY"
    RETIRING = "RETIRING"
    EXITED = "EXITED"


@dataclass(slots=True)
class WorkerHandle:
    """Everything the master knows about one worker process (spec/04 §2).

    Why:
        One record per process; `kill_sent` remembers whether *we* sent a kill, so a SIGKILL
        we didn't send can be flagged as a suspected OOM (spec/04 §4.1).
    """

    worker_id: int
    generation: int
    pid: int
    conn: Connection
    launched_mono: float
    state: WorkerState = WorkerState.BOOTING
    capacity: int = 1
    inflight: dict[str, int] = field(default_factory=dict)  # job_id → attempt number
    jobs_done: int = 0
    last_heartbeat_mono: float = 0.0
    rss_bytes: int | None = None
    retire_reason: str | None = None
    kill_sent: signal.Signals | None = None
    kill_reason: str | None = None
    deadline_timer: int | None = None
    manifest: dict[str, object] = field(default_factory=dict)

    @property
    def free_slots(self) -> int:
        """How many more jobs this worker can take right now.

        Returns:
            capacity minus in-flight jobs (never negative).
        """
        return max(0, self.capacity - len(self.inflight))


class WorkerTable:
    """All current workers, findable by id, by pid and by socket fd.

    Why:
        The loop gets events by fd (selector), deaths by pid (waitpid) and commands by id
        (retire worker 2); three dicts make each lookup instant.
    """

    def __init__(self) -> None:
        """Create an empty table."""
        self.by_id: dict[int, WorkerHandle] = {}
        self.by_pid: dict[int, WorkerHandle] = {}
        self.by_fd: dict[int, WorkerHandle] = {}

    def add(self, handle: WorkerHandle) -> None:
        """Start tracking a newly launched worker.

        Args:
            handle: the new worker.
        """
        self.by_id[handle.worker_id] = handle
        self.by_pid[handle.pid] = handle
        self.by_fd[handle.conn.fileno()] = handle

    def remove(self, handle: WorkerHandle) -> None:
        """Stop tracking a worker (after it was reaped).

        Why:
            All three lookup tables must forget the worker together, or a stale entry could route
            events to a dead worker.

        Args:
            handle: the worker.
        """
        if self.by_id.get(handle.worker_id) is handle:
            del self.by_id[handle.worker_id]
        self.by_pid.pop(handle.pid, None)
        for fd, other in list(self.by_fd.items()):
            if other is handle:
                del self.by_fd[fd]

    def forget_fd(self, handle: WorkerHandle) -> None:
        """Drop the fd mapping once a worker's channel is closed (the pid stays until reaped).

        Args:
            handle: the worker.
        """
        for fd, other in list(self.by_fd.items()):
            if other is handle:
                del self.by_fd[fd]

    def lowest_free_id(self) -> int:
        """Pick the smallest worker id not in use (ids are reused, spec/04 §2).

        Returns:
            The id.
        """
        worker_id = 0
        while worker_id in self.by_id:
            worker_id += 1
        return worker_id

    def all(self) -> list[WorkerHandle]:
        """Every tracked worker, by id.

        Returns:
            The handles.
        """
        return [self.by_id[worker_id] for worker_id in sorted(self.by_id)]

    def count_active(self) -> int:
        """Count workers that count toward the target (BOOTING or READY, spec/04 §1).

        Returns:
            The count.
        """
        return sum(
            1 for h in self.by_id.values() if h.state in (WorkerState.BOOTING, WorkerState.READY)
        )

    def ready_with_capacity(self) -> list[WorkerHandle]:
        """READY workers with a free slot, best first: most free slots, then lowest id.

        Why:
            spec/03 §6: least-loaded first, ties to the lowest id, so dispatch is
            deterministic and easy to test.

        Returns:
            The candidates in dispatch order.
        """
        # WHY: a worker the master is killing (kill_sent set) must not get new jobs; they'd be
        # lost when it dies (ADR-034).
        candidates = [
            h
            for h in self.by_id.values()
            if h.state is WorkerState.READY and h.free_slots > 0 and h.kill_sent is None
        ]
        candidates.sort(key=_dispatch_order)
        return candidates

    def inflight_total(self) -> int:
        """Count jobs running on any worker.

        Returns:
            The total.
        """
        return sum(len(h.inflight) for h in self.by_id.values())


def _dispatch_order(handle: WorkerHandle) -> tuple[int, int]:
    """Sort key: more free slots first (negated), then lower worker id.

    Args:
        handle: a worker.

    Returns:
        The sort key.
    """
    return (-handle.free_slots, handle.worker_id)
