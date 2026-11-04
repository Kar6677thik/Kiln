"""Master-side job table: the queue, every job's record, and result expiry.

WHY THIS MODULE EXISTS
    The master must remember every job it has accepted: which are waiting, which are running
    where, and which have finished, so it can answer `status`/`wait`, retry lost jobs and
    evict old results. All of that lives in one class with one set of rules: the state
    machine of docs/spec/03-jobs-and-tasks.md §4. Every state change goes through one
    function, _transition(), which refuses anything the table doesn't allow.

HOW IT FITS
    arbiter.py  ──calls──▶  JobTable.submit / .cancel / .view / .stats
    dispatcher.py ──calls──▶ JobTable.pop_next / .start / .push_front
    supervisor.py ──calls──▶ JobTable.fail_attempt (worker died, job timed out)
    JobTable ──on_terminal(job)──▶ arbiter wakes any client waiting on that job
    It is pure logic: no sockets and no clock calls. Time comes in as arguments, so tests
    are exact and repeatable.

READ FIRST
    docs/spec/03-jobs-and-tasks.md, docs/concepts/state-machines.md,
    docs/python/collections-deque.md, docs/python/heapq.md, docs/python/uuid.md

MILESTONE
    Introduced in K1.
"""

import heapq
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from kiln.errors import ErrorCode, KilnError


class JobState(StrEnum):
    """The seven states a job can be in (spec/03 §4).

    Why:
        Named constants make the transition table readable and typo-proof.
    """

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    LOST = "LOST"
    CANCELLED = "CANCELLED"


TERMINAL = frozenset(
    {JobState.SUCCEEDED, JobState.FAILED, JobState.TIMED_OUT, JobState.LOST, JobState.CANCELLED}
)

# WHY: a literal copy of the transition table in spec/03 §4.1, as data. Each allowed
# (from, to) pair maps to the event that causes it. _transition() checks this table, so a
# bug that tries any other move fails loudly instead of corrupting a job.
_ALLOWED: dict[tuple[JobState, JobState], str] = {
    (JobState.QUEUED, JobState.RUNNING): "dispatched to a worker slot",
    (JobState.QUEUED, JobState.CANCELLED): "cancel request or master shutdown",
    (JobState.QUEUED, JobState.FAILED): "task missing from the manifest at dispatch",
    (JobState.RUNNING, JobState.SUCCEEDED): "result ok",
    (JobState.RUNNING, JobState.FAILED): "result error, no retry",
    (JobState.RUNNING, JobState.TIMED_OUT): "timeout, no retry",
    (JobState.RUNNING, JobState.LOST): "worker died, no retry",
    (JobState.RUNNING, JobState.QUEUED): "retry after error, timeout or worker loss",
}

# WHY: which failure kind leads to which terminal state when no retry is allowed.
_FAILURE_STATE = {
    "exception": JobState.FAILED,
    "serialization": JobState.FAILED,
    "timeout": JobState.TIMED_OUT,
    "worker_lost": JobState.LOST,
}


class QueueFull(KilnError):
    """The queue (or the job table) is at its limit; the client should retry later.

    Why:
        Backpressure: refuse new work instead of growing without bound (spec/03 §3.1).
    """

    def __init__(self, message: str) -> None:
        """Create the error with code QUEUE_FULL.

        Args:
            message: details for the client.
        """
        super().__init__(message, ErrorCode.QUEUE_FULL)


class NotFound(KilnError):
    """No job with this id (never existed, or already evicted after its TTL).

    Why:
        Maps to the KP/1 NOT_FOUND reply.
    """

    def __init__(self, job_id: str) -> None:
        """Create the error with code NOT_FOUND.

        Args:
            job_id: the id that was asked for.
        """
        super().__init__(f"no job {job_id!r} (it may have expired)", ErrorCode.NOT_FOUND)


class NotCancellable(KilnError):
    """Only QUEUED jobs can be cancelled.

    Why:
        Stopping a running Python function safely isn't possible in general (spec/00 §4).
    """

    def __init__(self, job_id: str, state: JobState) -> None:
        """Create the error with code NOT_CANCELLABLE.

        Args:
            job_id: the job.
            state: its current state.
        """
        super().__init__(
            f"job {job_id} is {state}, only QUEUED jobs can be cancelled", ErrorCode.NOT_CANCELLABLE
        )


@dataclass(slots=True)
class Attempt:
    """One try at running a job on a worker.

    Why:
        A retried job has several attempts; the attempt number lets the master ignore late
        results from attempts it has already given up on.
    """

    n: int
    worker_id: int
    pid: int
    started_mono: float
    ended_mono: float | None = None
    outcome: str | None = None  # "succeeded", "exception", "timeout", "worker_lost", ...
    duration_ms: float | None = None


@dataclass(slots=True)
class Job:
    """Everything the master knows about one job (spec/03 §3).

    Why:
        A plain record; all the rules about *changing* it live in JobTable.
    """

    job_id: str
    task: str
    args: list[object]
    kwargs: dict[str, object]
    timeout_s: float
    max_attempts: int
    retry_on: frozenset[str]
    submitted_mono: float
    submitted_wall_ms: int
    state: JobState = JobState.QUEUED
    started_wall_ms: int | None = None
    finished_wall_ms: int | None = None
    attempts: list[Attempt] = field(default_factory=list)
    result: object = None
    error: dict[str, object] | None = None
    expires_mono: float | None = None


def _transition(job: Job, to: JobState) -> None:
    """Move a job to a new state, if and only if the transition table allows it.

    Why:
        The single place a state changes (K1 spec §5.5). A terminal job never changes again:
        that's the most important invariant of the job model, and this is where it's enforced.

    Args:
        job: the job.
        to: the new state.

    Raises:
        AssertionError: the move isn't in _ALLOWED (a bug in Kiln, never a user error).
    """
    # INVARIANT: only transitions listed in spec/03 §4.1 ever happen.
    if (job.state, to) not in _ALLOWED:
        raise AssertionError(f"illegal job transition {job.state} → {to} for {job.job_id}")
    job.state = to


class JobTable:
    """All jobs, the FIFO queue of waiting job ids, and the expiry heap of finished jobs.

    Why:
        Keeping the three structures in one class (and changing them only in its methods)
        guarantees they always agree: the queue holds exactly the QUEUED jobs, the heap holds
        exactly the finished ones (tested by tests/unit/test_jobtable_state_machine.py).
    """

    def __init__(self, max_queued: int, max_retained: int, result_ttl_s: float) -> None:
        """Create an empty table.

        Why:
            The three limits come from config (spec/03 §3.1), so tests can build tiny tables to hit
            them quickly.

        Args:
            max_queued: queue.max_size; more QUEUED jobs → QueueFull.
            max_retained: jobs.max_retained; total jobs kept (terminal ones are evicted first).
            result_ttl_s: how long finished jobs are kept.
        """
        self.max_queued = max_queued
        self.max_retained = max_retained
        self.result_ttl_s = result_ttl_s
        self.jobs: dict[str, Job] = {}
        # PY: deque = double-ended queue: fast append on the right, fast pop on the left.
        # See docs/python/collections-deque.md.
        self.queue: deque[str] = deque()
        # PY: a list kept in heap order by heapq: the smallest (earliest expiry) is first.
        self._expiry: list[tuple[float, str]] = []
        self.on_terminal: Callable[[Job], None] = _ignore
        self.counters = {"submitted": 0, "rejected": 0, "retried": 0, "evicted": 0}

    # --- creating and dispatching ------------------------------------------------------------

    def submit(
        self,
        task: str,
        args: list[object],
        kwargs: dict[str, object],
        options: tuple[float, int, frozenset[str]],
        now_mono: float,
        now_wall_ms: int,
    ) -> Job:
        """Accept a new job into the queue.

        Why:
            Backpressure happens here: a full queue refuses work instead of growing without bound
            (spec/03 §3.1).

        Args:
            task: the task name (already checked against the manifest by the caller).
            args: positional arguments.
            kwargs: keyword arguments.
            options: (timeout_s, max_attempts, retry_on), already resolved from submit,
                task and config defaults.
            now_mono: time.monotonic() now.
            now_wall_ms: wall-clock milliseconds now (for humans).

        Returns:
            The new job, state QUEUED.

        Raises:
            QueueFull: the queue is at max_queued, or the table is full of unfinished jobs.
        """
        if len(self.queue) >= self.max_queued:
            self.counters["rejected"] += 1
            raise QueueFull(f"queue has {len(self.queue)}/{self.max_queued} jobs")
        if len(self.jobs) >= self.max_retained and self.evict_for_capacity() == 0:
            self.counters["rejected"] += 1
            raise QueueFull(f"job table holds {len(self.jobs)} unfinished jobs")
        timeout_s, max_attempts, retry_on = options
        job = Job(
            job_id=uuid.uuid4().hex,  # PY: a random 32-hex-character id; see docs/python/uuid.md
            task=task,
            args=args,
            kwargs=kwargs,
            timeout_s=timeout_s,
            max_attempts=max_attempts,
            retry_on=retry_on,
            submitted_mono=now_mono,
            submitted_wall_ms=now_wall_ms,
        )
        self.jobs[job.job_id] = job
        self.queue.append(job.job_id)
        self.counters["submitted"] += 1
        return job

    def pop_next(self) -> Job | None:
        """Take the oldest QUEUED job off the queue (FIFO).

        Returns:
            The job, still QUEUED, or None if the queue is empty.
        """
        if not self.queue:
            return None
        return self.jobs[self.queue.popleft()]

    def push_front(self, job: Job) -> None:
        """Put a job back at the *front* of the queue (the dispatcher couldn't place it).

        Args:
            job: a QUEUED job previously returned by pop_next().
        """
        self.queue.appendleft(job.job_id)

    def start(
        self, job: Job, worker_id: int, pid: int, now_mono: float, now_wall_ms: int
    ) -> Attempt:
        """Mark a job RUNNING on a worker and open a new attempt.

        Why:
            Opening a numbered attempt is what lets the master later tell a current result from a
            late one.

        Args:
            job: a job just taken off the queue.
            worker_id: the worker that will run it.
            pid: that worker's process id.
            now_mono: monotonic time now.
            now_wall_ms: wall-clock ms now.

        Returns:
            The new attempt (its number goes into the `run` message).
        """
        _transition(job, JobState.RUNNING)
        if job.started_wall_ms is None:
            job.started_wall_ms = now_wall_ms
        attempt = Attempt(
            n=len(job.attempts) + 1, worker_id=worker_id, pid=pid, started_mono=now_mono
        )
        job.attempts.append(attempt)
        return attempt

    # --- finishing ---------------------------------------------------------------------------

    def _current_attempt(self, job_id: str, attempt_n: int) -> tuple[Job, Attempt] | None:
        """Find a RUNNING job whose current attempt has this number, or None if stale.

        Why:
            spec/03 §4.1 "late result": a result (or timeout) for an attempt we already gave up
            on must change nothing. Matching (job_id, attempt number) catches that.

        Args:
            job_id: the job.
            attempt_n: the attempt number reported.

        Returns:
            (job, attempt) if it matches the running attempt, else None.
        """
        job = self.jobs.get(job_id)
        if job is None or job.state is not JobState.RUNNING or not job.attempts:
            return None
        attempt = job.attempts[-1]
        if attempt.n != attempt_n or attempt.ended_mono is not None:
            return None
        return job, attempt

    def succeed(
        self,
        job_id: str,
        attempt_n: int,
        value: object,
        duration_ms: float,
        now_mono: float,
        now_wall_ms: int,
    ) -> Job | None:
        """Record a successful result.

        Why:
            Stale results (from attempts already given up on) must change nothing, so this returns
            None for them.

        Args:
            job_id: the job.
            attempt_n: the attempt that produced the result.
            value: the task's return value.
            duration_ms: how long the task ran (measured by the worker).
            now_mono: monotonic time now.
            now_wall_ms: wall-clock ms now.

        Returns:
            The job, now SUCCEEDED, or None if this was a stale/unknown result.
        """
        found = self._current_attempt(job_id, attempt_n)
        if found is None:
            return None
        job, attempt = found
        self._close_attempt(attempt, "succeeded", now_mono, duration_ms)
        job.result = value
        self._finish(job, JobState.SUCCEEDED, now_mono, now_wall_ms)
        return job

    def fail_attempt(
        self,
        job_id: str,
        attempt_n: int,
        kind: str,
        error: dict[str, object],
        now_mono: float,
        now_wall_ms: int,
    ) -> Job | None:
        """Record a failed attempt; retry the job if allowed, otherwise finish it.

        Why:
            One path for every failure kind (exception, serialization, timeout, worker_lost),
            so the retry rule is written exactly once: retry only if the kind is in the job's
            retry_on AND the attempt budget isn't used up (spec/03 §4.1).

        Args:
            job_id: the job.
            attempt_n: the failed attempt's number.
            kind: "exception", "serialization", "timeout" or "worker_lost".
            error: {"type", "message", "traceback"} details (the "kind" is added here).
            now_mono: monotonic time now.
            now_wall_ms: wall-clock ms now.

        Returns:
            The job (QUEUED again, or terminal), or None for a stale attempt.
        """
        found = self._current_attempt(job_id, attempt_n)
        if found is None:
            return None
        job, attempt = found
        self._close_attempt(attempt, kind, now_mono, None)
        job.error = {"kind": kind, **error}
        retryable = kind in job.retry_on and kind != "serialization"
        if retryable and len(job.attempts) < job.max_attempts:
            _transition(job, JobState.QUEUED)
            # WHY: retries go to the BACK of the queue (spec/03 §6), behind newer jobs.
            self.queue.append(job.job_id)
            self.counters["retried"] += 1
            return job
        self._finish(job, _FAILURE_STATE[kind], now_mono, now_wall_ms)
        return job

    def fail_unknown_task(self, job: Job, now_mono: float, now_wall_ms: int) -> None:
        """Fail a queued job whose task no longer exists (it disappeared in a reload).

        Why:
            After a reload a queued job's task may no longer exist; it fails cleanly instead of
            reaching a worker.

        Args:
            job: a QUEUED job taken off the queue.
            now_mono: monotonic time now.
            now_wall_ms: wall-clock ms now.
        """
        job.error = {
            "kind": "unknown_task",
            "type": "UnknownTask",
            "message": f"task {job.task!r} is not in the current manifest",
            "traceback": "",
        }
        self._finish(job, JobState.FAILED, now_mono, now_wall_ms)

    def cancel(self, job_id: str, now_mono: float, now_wall_ms: int) -> Job:
        """Cancel a QUEUED job at a client's request.

        Why:
            Only queued jobs can be stopped safely (spec/00 §4); running ones would need to kill a
            worker.

        Args:
            job_id: the job.
            now_mono: monotonic time now.
            now_wall_ms: wall-clock ms now.

        Returns:
            The job, now CANCELLED.

        Raises:
            NotFound: unknown job id.
            NotCancellable: the job isn't QUEUED.
        """
        job = self.jobs.get(job_id)
        if job is None:
            raise NotFound(job_id)
        if job.state is not JobState.QUEUED:
            raise NotCancellable(job_id, job.state)
        self.queue.remove(job_id)
        self._cancel(job, "cancelled by client", now_mono, now_wall_ms)
        return job

    def cancel_all_queued(self, message: str, now_mono: float, now_wall_ms: int) -> list[Job]:
        """Cancel every QUEUED job (graceful shutdown, spec/04 §5.6).

        Why:
            Graceful shutdown must answer every waiting client rather than leave queued jobs
            unanswered (spec/04 §5.6).

        Args:
            message: why, shown in each job's error.
            now_mono: monotonic time now.
            now_wall_ms: wall-clock ms now.

        Returns:
            The cancelled jobs.
        """
        cancelled = []
        while self.queue:
            job = self.jobs[self.queue.popleft()]
            self._cancel(job, message, now_mono, now_wall_ms)
            cancelled.append(job)
        return cancelled

    def _cancel(self, job: Job, message: str, now_mono: float, now_wall_ms: int) -> None:
        """Finish a job as CANCELLED with an explanation.

        Args:
            job: a QUEUED job already removed from the queue.
            message: the reason.
            now_mono: monotonic time now.
            now_wall_ms: wall-clock ms now.
        """
        job.error = {"kind": "cancelled", "type": "Cancelled", "message": message, "traceback": ""}
        self._finish(job, JobState.CANCELLED, now_mono, now_wall_ms)

    def _close_attempt(
        self, attempt: Attempt, outcome: str, now_mono: float, duration_ms: float | None
    ) -> None:
        """Mark an attempt as ended.

        Args:
            attempt: the attempt.
            outcome: how it ended.
            now_mono: monotonic time now.
            duration_ms: the worker's measurement, or None to use master time.
        """
        attempt.ended_mono = now_mono
        attempt.outcome = outcome
        measured = (now_mono - attempt.started_mono) * 1000
        attempt.duration_ms = duration_ms if duration_ms is not None else measured

    def _finish(self, job: Job, state: JobState, now_mono: float, now_wall_ms: int) -> None:
        """Make a job terminal, schedule its expiry and notify waiters.

        Args:
            job: the job.
            state: a terminal state.
            now_mono: monotonic time now.
            now_wall_ms: wall-clock ms now.
        """
        _transition(job, state)
        job.finished_wall_ms = now_wall_ms
        job.expires_mono = now_mono + self.result_ttl_s
        heapq.heappush(self._expiry, (job.expires_mono, job.job_id))
        self.on_terminal(job)

    # --- eviction ----------------------------------------------------------------------------

    def evict_expired(self, now_mono: float) -> int:
        """Forget finished jobs whose result_ttl_s has passed.

        Why:
            Results are kept for result_ttl_s only; without expiry the table would grow forever.

        Args:
            now_mono: monotonic time now.

        Returns:
            How many jobs were removed.
        """
        removed = 0
        while self._expiry and self._expiry[0][0] <= now_mono:
            _, job_id = heapq.heappop(self._expiry)
            if self.jobs.pop(job_id, None) is not None:
                removed += 1
        self.counters["evicted"] += removed
        return removed

    def evict_for_capacity(self) -> int:
        """Forget the oldest finished jobs early, to get back under max_retained.

        Why:
            When the table is full, finished jobs are dropped first so new work can still be
            accepted.

        Returns:
            How many jobs were removed (0 if every job is unfinished).
        """
        removed = 0
        while len(self.jobs) >= self.max_retained and self._expiry:
            _, job_id = heapq.heappop(self._expiry)
            if self.jobs.pop(job_id, None) is not None:
                removed += 1
        self.counters["evicted"] += removed
        return removed

    # --- reading -----------------------------------------------------------------------------

    def get(self, job_id: str) -> Job:
        """Look up a job.

        Args:
            job_id: the id.

        Returns:
            The job.

        Raises:
            NotFound: unknown or evicted.
        """
        job = self.jobs.get(job_id)
        if job is None:
            raise NotFound(job_id)
        return job

    def stats(self) -> dict[str, object]:
        """Counts per state plus totals (the "jobs" part of StatsView, spec/06 §3).

        Why:
            Counts are recomputed from the jobs themselves, so they can never drift from the truth.

        Returns:
            {"by_state": {...}, "retained": n, "totals_since_start": {...}}.
        """
        by_state = {str(state): 0 for state in JobState}
        for job in self.jobs.values():
            by_state[str(job.state)] += 1
        return {
            "by_state": by_state,
            "retained": len(self.jobs),
            "totals_since_start": dict(self.counters),
        }


def view(job: Job) -> dict[str, object]:
    """Render a job as the JobView clients see (spec/02 §3.5).

    Why:
        Clients see a stable JSON shape (spec/02 §3.5), independent of how Job stores its fields.

    Args:
        job: the job.

    Returns:
        The JobView dict.
    """
    data: dict[str, object] = {
        "job_id": job.job_id,
        "task": job.task,
        "state": str(job.state),
        "args": job.args,
        "kwargs": job.kwargs,
        "submitted_at_ms": job.submitted_wall_ms,
        "started_at_ms": job.started_wall_ms,
        "finished_at_ms": job.finished_wall_ms,
        "attempts": [
            {
                "n": a.n,
                "worker_id": a.worker_id,
                "pid": a.pid,
                "outcome": a.outcome,
                "duration_ms": a.duration_ms,
            }
            for a in job.attempts
        ],
        "error": job.error,
    }
    if job.state is JobState.SUCCEEDED:
        data["result"] = job.result
    return data


def _ignore(job: Job) -> None:
    """Default on_terminal callback: do nothing.

    Args:
        job: the job that just finished.
    """
