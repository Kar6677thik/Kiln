"""Deadlines for the master's event loop: "do X at time T", kept in a min-heap.

WHY THIS MODULE EXISTS
    The master has many things to do *later*: kill a job that runs past its timeout, answer
    a `wait` that times out, check heartbeats every second, respawn a worker after a backoff.
    The loop can't sleep for each one separately, because it has one thread (ADR-006). So
    every deadline goes into one heap, and before each select() the loop asks "when is the
    next deadline?" and waits at most that long (docs/concepts/event-loops.md).

HOW IT FITS
    arbiter / supervisor / dispatcher ──schedule(deadline, kind, key)──▶ TimerHeap
    arbiter loop ──next_deadline()──▶ select() timeout
    arbiter loop ──pop_expired(now)──▶ [Timer, ...] ──▶ handler per kind

READ FIRST
    docs/concepts/event-loops.md, docs/python/heapq.md, docs/python/time-monotonic.md

MILESTONE
    Introduced in K1.
"""

import heapq
import itertools
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Timer:
    """One scheduled deadline.

    Why:
        `kind` says what to do ("job_timeout", "respawn", ...) and `key` says to what (a job
        id, a worker id), so one heap can hold every kind of deadline.
    """

    deadline_mono: float
    timer_id: int
    kind: str
    key: object


class TimerHeap:
    """A min-heap of Timers with cheap cancellation.

    Why:
        Removing an item from the middle of a heap is slow, so cancel() only remembers the id
        ("lazy deletion"); cancelled timers are skipped when they reach the top.
    """

    def __init__(self) -> None:
        """Create an empty heap."""
        # WHY: (deadline, id, Timer) tuples: heapq compares tuples item by item, and the
        # unique id breaks ties so Timer objects themselves are never compared.
        self._heap: list[tuple[float, int, Timer]] = []
        self._cancelled: set[int] = set()
        self._ids = itertools.count(1)

    def schedule(self, deadline_mono: float, kind: str, key: object = None) -> int:
        """Add a deadline.

        Args:
            deadline_mono: when, in time.monotonic() seconds.
            kind: what kind of timer (the loop dispatches on this).
            key: what it's about.

        Returns:
            The timer id, for cancel().
        """
        timer_id = next(self._ids)
        timer = Timer(deadline_mono, timer_id, kind, key)
        heapq.heappush(self._heap, (deadline_mono, timer_id, timer))
        return timer_id

    def cancel(self, timer_id: int | None) -> None:
        """Cancel a timer (safe to call with None or an already-fired id).

        Args:
            timer_id: the id from schedule(), or None.
        """
        if timer_id is not None:
            self._cancelled.add(timer_id)

    def _drop_cancelled_top(self) -> None:
        """Remove cancelled timers sitting at the top of the heap.

        Why:
            Keeps next_deadline() honest: a cancelled timer must not wake the loop early.
        """
        while self._heap and self._heap[0][1] in self._cancelled:
            _, timer_id, _ = heapq.heappop(self._heap)
            self._cancelled.discard(timer_id)

    def next_deadline(self) -> float | None:
        """Return the earliest live deadline, or None if there are no timers.

        Returns:
            A time.monotonic() value or None.
        """
        self._drop_cancelled_top()
        return self._heap[0][0] if self._heap else None

    def pop_expired(self, now_mono: float) -> list[Timer]:
        """Remove and return every live timer whose deadline has passed, earliest first.

        Why:
            Cancelled timers stay in the heap (lazy deletion), so this skips them while collecting
            every due one.

        Args:
            now_mono: time.monotonic() now.

        Returns:
            The expired timers.
        """
        expired = []
        self._drop_cancelled_top()
        while self._heap and self._heap[0][0] <= now_mono:
            _, timer_id, timer = heapq.heappop(self._heap)
            if timer_id in self._cancelled:
                self._cancelled.discard(timer_id)
            else:
                expired.append(timer)
            self._drop_cancelled_top()
        return expired

    def __len__(self) -> int:
        """Count timers still in the heap (including cancelled ones not yet skipped).

        Returns:
            The heap size.
        """
        return len(self._heap)
