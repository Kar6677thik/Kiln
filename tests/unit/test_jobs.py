"""Example tests for the job state machine: one test per row of spec/03 §4.1."""

import pytest

from kiln.master.jobs import Job, JobState, JobTable, NotCancellable, NotFound, QueueFull, view

ALL = frozenset({"worker_lost", "timeout", "exception"})


def table(max_queued: int = 10, max_retained: int = 100) -> JobTable:
    """Build a small job table.

    Args:
        max_queued: queue limit.
        max_retained: table limit.

    Returns:
        The table.
    """
    return JobTable(max_queued, max_retained, result_ttl_s=60.0)


def submit(jobs: JobTable, attempts: int = 1, retry_on: frozenset[str] = ALL) -> Job:
    """Submit a job with fixed options at time 0.

    Args:
        jobs: the table.
        attempts: max_attempts.
        retry_on: retryable kinds.

    Returns:
        The job.
    """
    return jobs.submit("add", [1], {}, (30.0, attempts, retry_on), 0.0, 0)


def start(jobs: JobTable) -> Job:
    """Submit and dispatch one job.

    Args:
        jobs: the table.

    Returns:
        The running job.
    """
    job = jobs.pop_next()
    assert job is not None
    jobs.start(job, worker_id=0, pid=100, now_mono=1.0, now_wall_ms=1000)
    return job


def test_submit_queues_fifo() -> None:
    """Row 1: submit → QUEUED, in FIFO order."""
    jobs = table()
    first, second = submit(jobs), submit(jobs)
    assert first.state is JobState.QUEUED
    assert list(jobs.queue) == [first.job_id, second.job_id]


def test_dispatch_starts_attempt_one() -> None:
    """Row 2: QUEUED → RUNNING opens attempt 1."""
    jobs = table()
    submit(jobs)
    job = start(jobs)
    assert job.state is JobState.RUNNING
    assert job.attempts[0].n == 1


def test_result_ok_succeeds_and_wakes_waiters() -> None:
    """Row: RUNNING → SUCCEEDED stores the value and calls on_terminal."""
    jobs = table()
    finished: list[Job] = []
    jobs.on_terminal = finished.append
    submit(jobs)
    job = start(jobs)
    assert jobs.succeed(job.job_id, 1, 5, 0.1, 2.0, 2000) is job
    assert job.state is JobState.SUCCEEDED
    assert view(job)["result"] == 5
    assert finished == [job]


@pytest.mark.parametrize(
    ("kind", "terminal"),
    [
        ("exception", JobState.FAILED),
        ("timeout", JobState.TIMED_OUT),
        ("worker_lost", JobState.LOST),
        ("serialization", JobState.FAILED),
    ],
)
def test_failure_without_retry_is_terminal(kind: str, terminal: JobState) -> None:
    """Rows: each failure kind with no attempts left → its terminal state."""
    jobs = table()
    submit(jobs, attempts=1)
    job = start(jobs)
    jobs.fail_attempt(
        job.job_id, 1, kind, {"type": "X", "message": "m", "traceback": ""}, 2.0, 2000
    )
    assert job.state is terminal
    assert job.error is not None
    assert job.error["kind"] == kind


@pytest.mark.parametrize("kind", ["exception", "timeout", "worker_lost"])
def test_failure_with_retry_requeues_at_the_back(kind: str) -> None:
    """Rows: RUNNING → QUEUED when the kind is retryable and attempts remain; retries go last."""
    jobs = table()
    submit(jobs, attempts=2)
    job = start(jobs)
    later = submit(jobs)
    jobs.fail_attempt(
        job.job_id, 1, kind, {"type": "X", "message": "m", "traceback": ""}, 2.0, 2000
    )
    assert job.state is JobState.QUEUED
    assert list(jobs.queue) == [later.job_id, job.job_id]


def test_kind_not_in_retry_on_is_terminal_even_with_attempts_left() -> None:
    """With retry_on={"timeout"}, an exception fails the job despite max_attempts=3."""
    jobs = table()
    submit(jobs, attempts=3, retry_on=frozenset({"timeout"}))
    job = start(jobs)
    jobs.fail_attempt(
        job.job_id, 1, "exception", {"type": "X", "message": "", "traceback": ""}, 2.0, 2000
    )
    assert job.state is JobState.FAILED


def test_cancel_only_while_queued() -> None:
    """Rows: QUEUED → CANCELLED; RUNNING can't be cancelled; unknown ids are NOT_FOUND."""
    jobs = table()
    queued = submit(jobs)
    jobs.cancel(queued.job_id, 1.0, 1000)
    assert queued.state is JobState.CANCELLED
    submit(jobs)
    running = start(jobs)
    with pytest.raises(NotCancellable):
        jobs.cancel(running.job_id, 1.0, 1000)
    with pytest.raises(NotFound):
        jobs.cancel("nope", 1.0, 1000)


def test_late_result_changes_nothing() -> None:
    """A result for an old attempt is ignored (spec/03 §4.1, "late result")."""
    jobs = table()
    submit(jobs, attempts=2)
    job = start(jobs)
    jobs.fail_attempt(
        job.job_id, 1, "worker_lost", {"type": "X", "message": "", "traceback": ""}, 2.0, 2000
    )
    assert jobs.succeed(job.job_id, 1, "stale", 0.1, 3.0, 3000) is None
    assert job.state is JobState.QUEUED


def test_terminal_jobs_never_change_again() -> None:
    """The core invariant: once terminal, no transition is allowed (AssertionError if tried)."""
    jobs = table()
    submit(jobs)
    job = start(jobs)
    jobs.succeed(job.job_id, 1, 1, 0.1, 2.0, 2000)
    assert jobs.succeed(job.job_id, 1, 2, 0.1, 2.0, 2000) is None
    with pytest.raises(AssertionError):
        jobs.start(job, 0, 1, 3.0, 3000)


def test_queue_full_and_eviction() -> None:
    """QUEUE_FULL at queue.max_size; finished jobs expire after their TTL."""
    jobs = table(max_queued=1)
    submit(jobs)
    with pytest.raises(QueueFull):
        submit(jobs)
    job = start(jobs)
    jobs.succeed(job.job_id, 1, 1, 0.1, 2.0, 2000)
    assert jobs.evict_expired(now_mono=61.0) == 0
    assert jobs.evict_expired(now_mono=62.1) == 1
    assert job.job_id not in jobs.jobs


def test_shutdown_cancels_every_queued_job() -> None:
    """Graceful shutdown: every QUEUED job becomes CANCELLED with the reason."""
    jobs = table()
    submit(jobs)
    submit(jobs)
    cancelled = jobs.cancel_all_queued("master shutting down", 1.0, 1000)
    assert [j.state for j in cancelled] == [JobState.CANCELLED, JobState.CANCELLED]
    assert not jobs.queue
