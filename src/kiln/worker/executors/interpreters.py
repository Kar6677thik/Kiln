"""The `interpreters` executor: run up to N jobs at once in separate subinterpreters (3.14+).

WHY THIS MODULE EXISTS
    A subinterpreter is a second, independent Python inside the same process, with its own
    modules and, since PEP 684, its own GIL. N subinterpreters can run Python code in
    parallel even on the normal build, without sharing objects, so data races between them
    are hard to create by accident (docs/concepts/subinterpreters.md).

HOW IT FITS
    worker main thread ──submit(run, on_done)──▶ InterpreterPoolExecutor
        ──_run_in_interpreter(app_spec, sys_path, labs, task, args_json, kwargs_json)──▶
        a subinterpreter imports the app ONCE, runs the task, returns a JSON string
        ──future callback──▶ Result ──on_done──▶ channel.send (locked) ──▶ master

    We never send the task's function object, only its name and JSON strings: the
    subinterpreter imports the app itself and finds the task by name. Strings are the
    cheapest, most predictable thing to move between interpreters, and our payloads are
    JSON anyway (ADR-009).

READ FIRST
    docs/concepts/subinterpreters.md, docs/python/concurrent-futures.md

MILESTONE
    Introduced in K4.
"""

import json
import logging
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from typing import TYPE_CHECKING

from kiln import logsetup
from kiln.config import Config
from kiln.protocol.messages import Result, Run

if TYPE_CHECKING:
    from kiln.app import App

log = logging.getLogger(__name__)

# LAB: per-interpreter cache (code-style §6 exception). Each subinterpreter has its own copy
# of this module, so each imports the app once, on its first job.
_APPS: dict[str, "App"] = {}


def _run_in_interpreter(
    app_spec: str, sys_path_json: str, labs: bool, task: str, args_json: str, kwargs_json: str
) -> str:
    """Runs INSIDE a subinterpreter: import the app (once), run one task, answer in JSON.

    Why:
        Everything crossing the interpreter boundary is a str or bool. The function itself
        travels by its importable name (module + qualname), so it must live at module level.

    Args:
        app_spec: "module:attribute" of the app.
        sys_path_json: the main interpreter's sys.path, as JSON (a subinterpreter starts with
            the default path, without `kiln serve`'s additions).
        labs: whether to register the labs.* tasks.
        task: the task name.
        args_json: positional arguments, JSON.
        kwargs_json: keyword arguments, JSON.

    Returns:
        JSON: {"ok": true, "value": ..., "ms": ...} or {"ok": false, "error": {...}, "ms": ...}.
    """
    import asyncio  # WHY: imports run inside the subinterpreter, on demand
    import traceback

    started = time.perf_counter()

    def fail(error: BaseException) -> str:
        """Describe an exception as the shim's JSON answer (type, message, traceback).

        Why:
            Exceptions can't cross the interpreter boundary as objects; their text can.

        Args:
            error: the exception.

        Returns:
            The JSON answer.
        """
        ms = (time.perf_counter() - started) * 1000
        details = {
            "type": type(error).__name__,
            "message": str(error),
            "traceback": "".join(traceback.format_exception(error)),
        }
        return json.dumps({"ok": False, "error": details, "ms": ms})

    try:
        app = _APPS.get(app_spec)
        if app is None:
            sys.path[:] = json.loads(sys_path_json)
            from kiln.app import load_app  # WHY: inside the subinterpreter

            app = load_app(app_spec, [])
            if labs:
                from kiln.labs import tasks as lab_tasks

                lab_tasks.register(app)
            _APPS[app_spec] = app
        spec = app.get(task)
    except BaseException as error:  # noqa: BLE001  # WHY: an import failure is an answer too
        return fail(error)
    try:
        value = spec.func(*json.loads(args_json), **json.loads(kwargs_json))
        if spec.is_async:
            # Decision D4-1: async tasks run with asyncio.run inside the subinterpreter.
            value = asyncio.run(value)  # type: ignore[arg-type]  # WHY: is_async means func returns a coroutine
    except BaseException as error:  # noqa: BLE001  # WHY: one answer per run, always
        return fail(error)
    ms = (time.perf_counter() - started) * 1000
    try:
        return json.dumps({"ok": True, "value": value, "ms": ms}, allow_nan=False)
    except (TypeError, ValueError) as error:
        # WHY: the same check as value_result() in the main interpreter (ADR-009).
        details = {"type": "SerializationError", "message": str(error), "traceback": ""}
        return json.dumps({"ok": False, "error": details, "ms": ms})


class InterpretersExecutor:
    """A pool of subinterpreters (concurrent.futures.InterpreterPoolExecutor).

    Why:
        The standard pool already creates, reuses and shuts down the interpreters; we add
        the shim, the in-flight count and the translation back into a Result.
    """

    def __init__(self, config: Config, concurrency: int) -> None:
        """Create the pool (interpreters start lazily, on the first jobs).

        Why:
            Creating an interpreter costs an import of Kiln and the app, so they are made
            only when jobs need them, and then reused.

        Args:
            config: the configuration (app spec, labs flag).
            concurrency: worker.executor_concurrency.
        """
        from concurrent.futures import InterpreterPoolExecutor  # WHY: 3.14+ only

        self.name = "interpreters"
        self.capacity = concurrency
        self._config = config
        self._pool = InterpreterPoolExecutor(max_workers=concurrency)
        self._lock = threading.Lock()
        self._running = 0

    def submit(self, run: Run, on_done: Callable[[Result], None]) -> None:
        """Send the job to a subinterpreter and return at once.

        Why:
            The worker's main thread must stay free to read the channel; the pool's own
            thread waits for the subinterpreter and calls `done`.

        Args:
            run: the job.
            on_done: receives the Result, from the pool's thread.
        """
        with self._lock:
            self._running += 1
        future = self._pool.submit(
            _run_in_interpreter,
            self._config.app.spec,
            json.dumps(sys.path),
            self._config.labs.enabled,
            run.task,
            json.dumps(run.args),
            json.dumps(run.kwargs),
        )

        def done(finished: "Future[str]") -> None:
            """Turn the shim's JSON answer (or the pool's own error) into a Result.

            Why:
                Every run must get exactly one Result, even if the pool itself failed.

            Args:
                finished: the completed future.
            """
            try:
                answer = json.loads(finished.result())
            except BaseException as error:  # noqa: BLE001  # WHY: the pool itself failed
                answer = {
                    "ok": False,
                    "error": {"type": type(error).__name__, "message": str(error), "traceback": ""},
                    "ms": 0.0,
                }
            with self._lock:
                self._running -= 1
            if answer["ok"]:
                on_done(Result(run.job_id, run.attempt, True, answer["value"], None, answer["ms"]))
                return
            details = answer["error"]
            if details.get("type") in ("ImportError", "ModuleNotFoundError"):
                logsetup.event(
                    log,
                    logging.ERROR,
                    "interp_import_failed",
                    task=run.task,
                    error_type=details.get("type"),
                    error_message=details.get("message"),
                )
            on_done(Result(run.job_id, run.attempt, False, None, details, answer["ms"]))

        future.add_done_callback(done)

    def inflight(self) -> int:
        """How many jobs are queued or running.

        Returns:
            The count.
        """
        with self._lock:
            return self._running

    def shutdown(self, wait: bool) -> None:
        """Stop the pool and its interpreters.

        Args:
            wait: whether to wait for in-flight jobs.
        """
        self._pool.shutdown(wait=wait, cancel_futures=not wait)
