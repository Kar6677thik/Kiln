"""The Executor interface, plus run_task(): call one task and describe how it ended.

WHY THIS MODULE EXISTS
    Every executor must do the same few things: accept a job, run it, report the result,
    say how busy it is. A Protocol writes that down, so K4's executors can be swapped in
    without touching worker/main.py. run_task() holds the logic every executor shares: turn
    a task's return value, or its exception, into a `result` message.

HOW IT FITS
    worker/main.py ──executor.submit(run, on_done)──▶ SyncExecutor (K1) / ThreadExecutor (K4)...
    executor ──run_task(app, run)──▶ Result ──on_done(result)──▶ channel.send(result)

READ FIRST
    docs/python/typing-protocol.md, docs/python/exceptions.md, docs/python/json.md

MILESTONE
    Introduced in K1.
"""

import asyncio
import json
import time
import traceback
from collections.abc import Callable
from typing import Protocol

from kiln.app import App
from kiln.protocol.messages import Result, Run


class Executor(Protocol):
    """What every executor provides (K1 spec §5.8, finalized in K4 §5.1).

    Why:
        A Protocol describes the *shape* (methods and attributes) without forcing classes to
        inherit from anything; mypy checks that each executor matches. K4 keeps K1's shape:
        the per-job `on_done` callback *is* the spec's "result sink" (ADR-037). Rules: call
        on_done exactly once per `run`, from any thread (the channel's send is locked);
        `submit` returns quickly, except under `sync`, which runs the job inline.
    """

    name: str  # "sync" | "threads" | "interpreters" | "asyncio", reported in `ready`
    capacity: int  # how many jobs at once; the master never sends more

    def submit(self, run: Run, on_done: Callable[[Result], None]) -> None:
        """Start running one job; call on_done with its Result when it ends.

        Args:
            run: the job to run.
            on_done: called exactly once with the outcome.
        """
        ...

    def inflight(self) -> int:
        """Return how many jobs are running now.

        Returns:
            The count.
        """
        ...

    def shutdown(self, wait: bool) -> None:
        """Stop accepting jobs; optionally wait for running ones.

        Args:
            wait: whether to wait for in-flight jobs.
        """
        ...


def _error(kind: str, error: BaseException) -> dict[str, object]:
    """Describe an exception for the `result` message.

    Args:
        kind: error type name to report.
        error: the exception.

    Returns:
        {"type", "message", "traceback"}.
    """
    return {
        "type": kind,
        "message": str(error),
        "traceback": "".join(traceback.format_exception(error)),
    }


def unknown_task(run: Run) -> Result:
    """The Result for a `run` naming a task the app doesn't have.

    Why:
        Every executor must answer every `run`, even a bad one; this is that answer.

    Args:
        run: the job.

    Returns:
        A failed Result with error type "UnknownTask".
    """
    error: dict[str, object] = {
        "type": "UnknownTask",
        "message": f"no task named {run.task!r}",
        "traceback": "",
    }
    return Result(run.job_id, run.attempt, False, None, error, 0.0)


def error_result(
    run: Run, error: BaseException, elapsed_ms: float, *, timed_out: bool = False
) -> Result:
    """The Result for a task that raised (or, with timed_out, was cancelled by its timeout).

    Why:
        One place builds failure messages, so every executor reports errors identically.

    Args:
        run: the job.
        error: the exception.
        elapsed_ms: how long the attempt ran.
        timed_out: True if a cooperative timeout ended it (K4 asyncio executor).

    Returns:
        A failed Result.
    """
    return Result(
        run.job_id,
        run.attempt,
        False,
        None,
        _error(type(error).__name__, error),
        elapsed_ms,
        timed_out,
    )


def value_result(run: Run, value: object, elapsed_ms: float) -> Result:
    """The Result for a task that returned `value`, after checking it can travel as JSON.

    Why:
        Check now, in the worker, that the value is JSON-compatible (ADR-009); the master
        turns error type "SerializationError" into error kind "serialization".

    Args:
        run: the job.
        value: what the task returned.
        elapsed_ms: how long the attempt ran.

    Returns:
        A successful Result, or a failed one with error type "SerializationError".
    """
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as error:
        return Result(
            run.job_id, run.attempt, False, None, _error("SerializationError", error), elapsed_ms
        )
    return Result(run.job_id, run.attempt, True, value, None, elapsed_ms)


def run_task(app: App, run: Run) -> Result:
    """Run one task on the calling thread and turn whatever happened into a Result.

    Why:
        Every outcome must become a message: a return value (checked to be JSON-compatible),
        an exception, or an unknown task name. Nothing here may crash the worker, because the
        master is waiting for an answer to this attempt. Used by `sync` and `threads`.

    Args:
        app: the loaded app.
        run: the job.

    Returns:
        The Result to send back.
    """
    started = time.perf_counter()
    try:
        spec = app.get(run.task)
    except KeyError:
        return unknown_task(run)
    try:
        value = spec.func(*run.args, **run.kwargs)
        if spec.is_async:
            # WHY: a fresh event loop per job (K4 §5.6): simple and correct on any thread.
            value = asyncio.run(value)  # type: ignore[arg-type]  # WHY: is_async means func returns a coroutine
    except Exception as error:  # noqa: BLE001  # WHY: any task exception becomes a FAILED result, not a dead worker
        return error_result(run, error, (time.perf_counter() - started) * 1000)
    return value_result(run, value, (time.perf_counter() - started) * 1000)
