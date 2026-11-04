"""The `threads` executor: run up to N jobs at once on a pool of threads.

WHY THIS MODULE EXISTS
    One worker process can wait on many things at once (network calls, sleeps) if each job
    gets its own thread. On the normal (GIL) build, threads take turns running Python code,
    so CPU-heavy jobs don't speed up; on the free-threaded build (3.15t) they really run in
    parallel. Comparing the two is the point of K4's lab (docs/concepts/gil.md).

HOW IT FITS
    worker/main.py ──submit(run, on_done)──▶ ThreadPoolExecutor ──▶ "kiln-job_N" thread
        ──run_task(app, run)──▶ Result ──on_done(result)──▶ channel.send (locked) ──▶ master
    capacity = worker.executor_concurrency; the master never sends more runs than that.

READ FIRST
    docs/concepts/threads.md, docs/concepts/gil.md, docs/python/concurrent-futures.md,
    docs/python/locks.md

MILESTONE
    Introduced in K4.
"""

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from kiln.app import App
from kiln.protocol.messages import Result, Run
from kiln.worker.executors.base import error_result, run_task


class ThreadsExecutor:
    """A fixed-size thread pool; each job runs on one of its threads.

    Why:
        concurrent.futures already implements a correct pool (queueing, worker threads,
        shutdown); we add only the in-flight count and the result reporting.
    """

    def __init__(self, app: App, concurrency: int) -> None:
        """Create the pool (its threads start lazily, on the first jobs).

        Why:
            A fixed size equal to the capacity means the master's slot count and the real
            number of threads always agree.

        Args:
            app: the loaded app.
            concurrency: worker.executor_concurrency.
        """
        self.app = app
        self.name = "threads"
        self.capacity = concurrency
        # PY: thread_name_prefix makes the threads show up as "kiln-job_0", "kiln-job_1"...
        # in tracebacks and debuggers (gate G4.10).
        self._pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="kiln-job")
        self._lock = threading.Lock()
        self._running = 0

    def _run(self, run: Run, on_done: Callable[[Result], None]) -> None:
        """Body of one pool job: run the task, report it, keep the count right.

        Why:
            run_task already turns every Exception into a Result. Anything stranger
            (a task calling sys.exit, for instance) must still produce exactly one answer,
            or the master would wait for this attempt until its timeout.

        Args:
            run: the job.
            on_done: receives the Result.
        """
        started = time.perf_counter()
        try:
            result = run_task(self.app, run)
        except BaseException as error:  # noqa: BLE001  # WHY: one answer per run, always
            result = error_result(run, error, (time.perf_counter() - started) * 1000)
        finally:
            # CONCEPT(data-races): `self._running -= 1` is read-modify-write; two pool threads
            # doing it at once could lose an update, so it happens under the lock.
            with self._lock:
                self._running -= 1
        on_done(result)

    def submit(self, run: Run, on_done: Callable[[Result], None]) -> None:
        """Queue the job on the pool and return at once.

        Args:
            run: the job.
            on_done: receives the Result, from a pool thread.
        """
        with self._lock:
            self._running += 1
        self._pool.submit(self._run, run, on_done)

    def inflight(self) -> int:
        """How many jobs are queued or running.

        Returns:
            The count.
        """
        with self._lock:
            return self._running

    def shutdown(self, wait: bool) -> None:
        """Stop the pool; with wait=True, let in-flight jobs finish first.

        Args:
            wait: whether to wait for in-flight jobs.
        """
        self._pool.shutdown(wait=wait, cancel_futures=not wait)
