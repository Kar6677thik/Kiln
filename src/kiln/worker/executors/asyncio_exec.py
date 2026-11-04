"""The `asyncio` executor: run up to N jobs at once on one event loop, in one extra thread.

WHY THIS MODULE EXISTS
    `async def` tasks spend most of their time waiting (await on a socket, a sleep), and one
    event loop can interleave thousands of such waits on a single thread. This executor
    gives them that loop, plus something threads can't offer: *cooperative* timeouts. An
    async task that runs too long is cancelled at its next `await`, and the worker survives
    (docs/concepts/cpu-vs-io-bound.md, the cooperative-cancellation section).

HOW IT FITS
    worker main thread ──submit(run, on_done)──▶ run_coroutine_threadsafe ──▶ "kiln-loop"
        async task:  asyncio.timeout(timeout_s) + await func(...)   → timed_out on expiry
        sync task:   await asyncio.to_thread(func, ...) on "kiln-tothread_N" (no timeout)
        ──on_done(result)──▶ channel.send (locked) ──▶ master
    The master's timer for asyncio workers fires at timeout_s + jobs.cancel_grace_s, so an
    uncooperative task (one that blocks the loop) is still hard-killed (K4 spec §5.4).

READ FIRST
    docs/python/async-await-basics.md, docs/python/asyncio-run.md,
    docs/concepts/cpu-vs-io-bound.md, docs/concepts/event-loops.md

MILESTONE
    Introduced in K4.
"""

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import cast

from kiln.app import App
from kiln.protocol.messages import Result, Run
from kiln.worker.executors.base import error_result, unknown_task, value_result


class AsyncioExecutor:
    """An event loop running forever in its own thread, fed from the worker's main thread.

    Why:
        The main thread must stay free to read the channel (the same in every executor),
        so the loop lives in a dedicated thread and jobs cross over with
        run_coroutine_threadsafe, the one thread-safe door into a running loop.
    """

    def __init__(self, app: App, concurrency: int) -> None:
        """Start the loop thread and give it a correctly sized to_thread pool.

        Why:
            The loop must be running before the first `run` arrives; starting it here means
            submit() never has to check.

        Args:
            app: the loaded app.
            concurrency: worker.executor_concurrency.
        """
        self.app = app
        self.name = "asyncio"
        self.capacity = concurrency
        self._lock = threading.Lock()
        self._running = 0
        self._loop = asyncio.new_event_loop()
        # WHY: asyncio.to_thread uses the loop's default executor, normally
        # min(32, cpu_count + 4) threads: more than our capacity. Sync tasks would then run on
        # threads the master doesn't know about. Size it to match (K4 §5.4, §12).
        self._tothread = ThreadPoolExecutor(
            max_workers=concurrency, thread_name_prefix="kiln-tothread"
        )
        self._loop.set_default_executor(self._tothread)
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="kiln-loop", daemon=True
        )
        self._thread.start()

    async def _run_job(self, run: Run, on_done: Callable[[Result], None]) -> None:
        """Run one job on the loop and report it.

        Why:
            Async tasks get a cooperative timeout (asyncio.timeout). Sync tasks get none:
            cancelling the `await` would not stop their thread, which would keep running on
            a slot the master believes is free; the master's hard kill covers them.

        Args:
            run: the job.
            on_done: receives the Result.
        """
        started = time.perf_counter()
        try:
            try:
                spec = self.app.get(run.task)
            except KeyError:
                result = unknown_task(run)
            else:
                try:
                    if spec.is_async:
                        async with asyncio.timeout(run.timeout_s):
                            # PY: cast tells mypy what is_async guarantees: func returns
                            # a coroutine (an Awaitable).
                            coroutine = cast(Awaitable[object], spec.func(*run.args, **run.kwargs))
                            value = await coroutine
                    else:
                        value = await asyncio.to_thread(spec.func, *run.args, **run.kwargs)
                except TimeoutError as error:
                    elapsed = (time.perf_counter() - started) * 1000
                    result = error_result(run, error, elapsed, timed_out=True)
                except Exception as error:  # noqa: BLE001  # WHY: a task error is a FAILED result
                    result = error_result(run, error, (time.perf_counter() - started) * 1000)
                else:
                    result = value_result(run, value, (time.perf_counter() - started) * 1000)
        finally:
            with self._lock:
                self._running -= 1
        on_done(result)

    def submit(self, run: Run, on_done: Callable[[Result], None]) -> None:
        """Hand the job to the loop thread and return at once.

        Args:
            run: the job.
            on_done: receives the Result, from the loop thread.
        """
        with self._lock:
            self._running += 1
        # SYSCALL-free but thread-crossing: schedules the coroutine on the other thread's loop.
        asyncio.run_coroutine_threadsafe(self._run_job(run, on_done), self._loop)

    def inflight(self) -> int:
        """How many jobs are running on the loop.

        Returns:
            The count.
        """
        with self._lock:
            return self._running

    def shutdown(self, wait: bool) -> None:
        """Stop the loop (after in-flight jobs, if wait) and join its thread.

        Args:
            wait: whether to wait for in-flight jobs (the master's grace timeout still applies).
        """
        while wait and self.inflight():
            time.sleep(0.01)  # WHY: shutdown only; the worker has nothing else to do
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._tothread.shutdown(wait=wait, cancel_futures=not wait)
