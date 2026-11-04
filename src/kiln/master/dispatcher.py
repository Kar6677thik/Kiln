"""Hand queued jobs to free worker slots.

WHY THIS MODULE EXISTS
    "Which job goes to which worker, and when?" is a small but important rule: FIFO order for
    jobs, least-loaded worker first (ties to the lowest id), and only READY workers with a
    free slot (docs/spec/03-jobs-and-tasks.md §6). Keeping the rule in one function makes it
    easy to read and to test.

HOW IT FITS
    arbiter loop (after any event that could free a slot or add a job) ──dispatch(arbiter)──▶
        jobs.pop_next() ──▶ jobs.start() ──▶ timers.schedule(job_timeout) ──▶ worker.conn.send(run)

READ FIRST
    docs/spec/03-jobs-and-tasks.md §6, docs/spec/01-architecture.md §6 (the life of one job)

MILESTONE
    Introduced in K1.
"""

import logging
import time
from typing import TYPE_CHECKING

from kiln import logsetup
from kiln.protocol.messages import MsgType, Run, message

# PY: TYPE_CHECKING is False at run time and True for mypy, so this import exists only for
# type hints, which avoids a circular import with arbiter.py. See docs/python/type-hints.md.
if TYPE_CHECKING:
    from kiln.master.arbiter import Arbiter

log = logging.getLogger(__name__)


def dispatch(arb: "Arbiter") -> int:
    """Send as many queued jobs as there are free READY worker slots.

    Why:
        Called after every event that adds a job or frees a slot. It stops as soon as either
        the queue or the free slots run out, so it's cheap to call often.

    Args:
        arb: the arbiter (job table, workers, timers, manifest).

    Returns:
        How many jobs were dispatched.
    """
    sent = 0
    while arb.jobs.queue:
        candidates = arb.workers.ready_with_capacity()
        if not candidates:
            break
        job = arb.jobs.pop_next()
        if job is None:
            break
        now = time.monotonic()
        wall = int(time.time() * 1000)
        if job.task not in arb.manifest_tasks():
            # WHY: the task vanished in a reload (spec/04 §5.2 step 6).
            arb.jobs.fail_unknown_task(job, now, wall)
            continue
        worker = candidates[0]
        attempt = arb.jobs.start(job, worker.worker_id, worker.pid, now, wall)
        worker.inflight[job.job_id] = attempt.n
        timer_id = arb.timers.schedule(now + job.timeout_s, "job_timeout", (job.job_id, attempt.n))
        arb.job_timers[(job.job_id, attempt.n)] = timer_id
        run = Run(job.job_id, attempt.n, job.task, job.args, job.kwargs, job.timeout_s)
        arb.send(worker.conn, message(MsgType.RUN, run))
        logsetup.event(
            log,
            logging.DEBUG,
            "job_dispatched",
            job_id=job.job_id,
            attempt=attempt.n,
            worker_id=worker.worker_id,
            wait_ms=round((now - job.submitted_mono) * 1000, 2),
        )
        sent += 1
    return sent
