"""Stateful property test of the JobTable (K1 spec §6.1, spec/09 §5.1).

What is property-based stateful testing? Instead of writing a few example sequences by hand,
we describe the operations that can happen (submit, dispatch, results, timeouts, cancels...)
and the facts that must ALWAYS hold (invariants). Hypothesis then generates hundreds of random
sequences of operations, checks every invariant after every step, and if anything breaks it
shrinks the sequence to the shortest one that still fails. It finds orderings a human never
thinks to write down, such as "a late result arrives right after a retry".
"""

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from kiln.master.jobs import TERMINAL, JobState, JobTable

KINDS = ["exception", "timeout", "worker_lost"]


class JobTableMachine(RuleBasedStateMachine):
    """Drives a real JobTable with random operations and checks the invariants each step.

    Why:
        AC-1.2: ≥ 500 examples with all 6 invariants holding.
    """

    def __init__(self) -> None:
        """Start with an empty table and an empty record of terminal states."""
        super().__init__()
        self.jobs = JobTable(max_queued=20, max_retained=200, result_ttl_s=5.0)
        self.now = 0.0
        self.frozen: dict[str, JobState] = {}
        self.stale: list[tuple[str, int]] = []

    def _running(self) -> list[str]:
        """Ids of RUNNING jobs.

        Returns:
            The ids.
        """
        return [job_id for job_id, job in self.jobs.jobs.items() if job.state is JobState.RUNNING]

    @rule(attempts=st.integers(min_value=1, max_value=3), retry=st.sets(st.sampled_from(KINDS)))
    def submit(self, attempts: int, retry: set[str]) -> None:
        """Submit a job (ignored if the queue is full).

        Args:
            attempts: max_attempts.
            retry: retry_on kinds.
        """
        if len(self.jobs.queue) < self.jobs.max_queued:
            self.jobs.submit("t", [], {}, (1.0, attempts, frozenset(retry)), self.now, 0)

    @precondition(lambda self: bool(self.jobs.queue))
    @rule()
    def dispatch_one(self) -> None:
        """Start the next queued job on a fake worker."""
        job = self.jobs.pop_next()
        assert job is not None
        self.jobs.start(job, 0, 1, self.now, 0)

    @precondition(lambda self: bool(self._running()))
    @rule(data=st.data())
    def result_ok(self, data: st.DataObject) -> None:
        """A running job succeeds.

        Args:
            data: Hypothesis data source.
        """
        job_id = data.draw(st.sampled_from(self._running()))
        attempt = self.jobs.jobs[job_id].attempts[-1].n
        self.jobs.succeed(job_id, attempt, 1, 0.1, self.now, 0)

    @precondition(lambda self: bool(self._running()))
    @rule(data=st.data(), kind=st.sampled_from(KINDS))
    def failure(self, data: st.DataObject, kind: str) -> None:
        """A running job fails (error, timeout or worker loss); remember its attempt as stale.

        Args:
            data: Hypothesis data source.
            kind: the failure kind.
        """
        job_id = data.draw(st.sampled_from(self._running()))
        attempt = self.jobs.jobs[job_id].attempts[-1].n
        self.jobs.fail_attempt(
            job_id, attempt, kind, {"type": "X", "message": "", "traceback": ""}, self.now, 0
        )
        self.stale.append((job_id, attempt))

    @precondition(lambda self: bool(self.stale))
    @rule(data=st.data())
    def stale_result(self, data: st.DataObject) -> None:
        """A result arrives for an attempt that already failed: nothing may change.

        Args:
            data: Hypothesis data source.
        """
        job_id, attempt = data.draw(st.sampled_from(self.stale))
        job = self.jobs.jobs.get(job_id)
        before = (job.state, len(job.attempts)) if job else None
        assert self.jobs.succeed(job_id, attempt, "late", 0.1, self.now, 0) is None
        after = (job.state, len(job.attempts)) if job else None
        assert before == after  # INVARIANT 6: a stale result never changes anything

    @precondition(lambda self: bool(self.jobs.queue))
    @rule(data=st.data())
    def cancel(self, data: st.DataObject) -> None:
        """Cancel a queued job.

        Args:
            data: Hypothesis data source.
        """
        job_id = data.draw(st.sampled_from(list(self.jobs.queue)))
        self.jobs.cancel(job_id, self.now, 0)

    @rule(seconds=st.floats(min_value=0.0, max_value=3.0))
    def advance_time(self, seconds: float) -> None:
        """Let time pass and evict expired results.

        Args:
            seconds: how much time passes.
        """
        self.now += seconds
        self.jobs.evict_expired(self.now)

    @invariant()
    def terminal_jobs_never_change(self) -> None:
        """Invariant 1: a job seen in a terminal state stays in that state."""
        for job_id, state in list(self.frozen.items()):
            job = self.jobs.jobs.get(job_id)
            if job is None:
                del self.frozen[job_id]
                continue
            assert job.state is state
        for job_id, job in self.jobs.jobs.items():
            if job.state in TERMINAL:
                self.frozen.setdefault(job_id, job.state)

    @invariant()
    def queue_is_exactly_the_queued_jobs(self) -> None:
        """Invariant 2: the queue holds exactly the QUEUED jobs, each once."""
        queued = {job_id for job_id, job in self.jobs.jobs.items() if job.state is JobState.QUEUED}
        assert sorted(self.jobs.queue) == sorted(queued)
        assert len(set(self.jobs.queue)) == len(self.jobs.queue)

    @invariant()
    def attempts_within_budget(self) -> None:
        """Invariant 3: no job has more attempts than max_attempts."""
        for job in self.jobs.jobs.values():
            assert len(job.attempts) <= job.max_attempts

    @invariant()
    def running_jobs_have_one_open_attempt(self) -> None:
        """Invariant 4: every RUNNING job has exactly one open attempt, its last one."""
        for job in self.jobs.jobs.values():
            open_attempts = [a for a in job.attempts if a.ended_mono is None]
            assert len(open_attempts) == (1 if job.state is JobState.RUNNING else 0)

    @invariant()
    def stats_match_the_jobs(self) -> None:
        """Invariant 5: stats() counts equal counts recomputed from the jobs."""
        by_state = self.jobs.stats()["by_state"]
        assert isinstance(by_state, dict)
        for state in JobState:
            assert by_state[str(state)] == sum(
                1 for j in self.jobs.jobs.values() if j.state is state
            )


JobTableMachine.TestCase.settings = settings(
    max_examples=500, stateful_step_count=30, deadline=None
)
test_jobtable_state_machine = JobTableMachine.TestCase
