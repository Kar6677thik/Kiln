"""The sync executor: run each job inline on the main thread, one at a time (capacity 1).

WHY THIS MODULE EXISTS
    The simplest possible executor, and the right one for K1: while a job runs, the worker
    does nothing else (except the heartbeat thread). It is also what classic pre-fork
    servers (gunicorn's sync worker, Celery's prefork pool) do.

HOW IT FITS
    worker/main.py ──submit(run, on_done)──▶ run_task(app, run) ──▶ on_done(result)  (same thread)

READ FIRST
    docs/spec/01-architecture.md §2.2

MILESTONE
    Introduced in K1.
"""

from collections.abc import Callable

from kiln.app import App
from kiln.protocol.messages import Result, Run
from kiln.worker.executors.base import run_task


class SyncExecutor:
    """Runs one job at a time, synchronously.

    Why:
        Capacity 1 means the master never sends a second job before the first finishes.
    """

    def __init__(self, app: App) -> None:
        """Create the executor.

        Args:
            app: the loaded app whose tasks it runs.
        """
        self.app = app
        self.name = "sync"
        self.capacity = 1
        self._running = 0

    def submit(self, run: Run, on_done: Callable[[Result], None]) -> None:
        """Run the job now and report it before returning.

        Why:
            Running the task inline is what capacity 1 means: the worker is busy until the result is
            sent.

        Args:
            run: the job.
            on_done: receives the Result.
        """
        self._running = 1
        try:
            result = run_task(self.app, run)
        finally:
            self._running = 0
        on_done(result)

    def inflight(self) -> int:
        """Return 1 while a job runs, else 0.

        Returns:
            The count.
        """
        return self._running

    def shutdown(self, wait: bool) -> None:
        """Nothing to stop: jobs run inline.

        Args:
            wait: ignored.
        """
