"""The task API users write against: `app = kiln.App("demo")` and `@app.task`.

WHY THIS MODULE EXISTS
    Kiln runs *your* functions. It needs a way to know which functions exist, under which
    names, and with which options (timeout, retries). An App object is that registry: the
    `@app.task` decorator records each function, and the worker later looks tasks up by name.
    The rules (names, options, JSON-only arguments) are in docs/spec/03-jobs-and-tasks.md §2.

HOW IT FITS
    user code: examples/demo.py ──@app.task──▶ App._tasks["add"] = TaskSpec(...)
    worker/main.py ──load_app("examples.demo:app")──▶ App ──manifest()──▶ `ready` message
    worker executor ──app.get("add").func(2, 3)──▶ 5
    The master never calls task functions (ADR-006); it only sees the manifest.

READ FIRST
    docs/python/decorators.md (both @app.task and @app.task(...) forms), docs/python/importlib.md,
    docs/python/inspect.md, docs/spec/03-jobs-and-tasks.md §2

MILESTONE
    Introduced in K1.
"""

import importlib
import inspect
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from kiln.config import JobsConfig
from kiln.errors import KilnError

# WHY: spec/03 §2.1: a letter or underscore, then letters, digits, "_" or "."; ≤ 128 chars.
TASK_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")
RETRY_KINDS = frozenset({"worker_lost", "timeout", "exception"})
DEFAULT_RETRY_ON = frozenset({"worker_lost", "timeout"})


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """One registered task: its name, its function and its options.

    Why:
        The registry stores this instead of the bare function, so options travel with it.
        None means "use the config default" (spec/03 §2.1).
    """

    name: str
    func: Callable[..., object]
    timeout_s: float | None
    max_attempts: int | None
    retry_on: frozenset[str] | None
    is_async: bool


class AppLoadError(KilnError):
    """The app spec couldn't be imported or didn't name an App.

    Why:
        A worker that fails to load the app reports this (boot_failed, exit 3), so the
        message must say what to fix.
    """


class App:
    """A named registry of tasks.

    Why:
        The user owns the App object; Kiln finds it through the "module:attribute" spec.
        Keeping the registry inside an instance (not a global) means tests can create as many
        independent apps as they like (code-style §6).
    """

    def __init__(self, name: str) -> None:
        """Create an empty app.

        Args:
            name: a label used only in logs.
        """
        self.name = name
        self._tasks: dict[str, TaskSpec] = {}

    def task(
        self,
        func: Callable[..., object] | None = None,
        /,
        *,
        name: str | None = None,
        timeout_s: float | None = None,
        max_attempts: int | None = None,
        retry_on: Sequence[str] | None = None,
    ) -> Any:  # noqa: ANN401  # WHY: returns a function OR a decorator; see the docstring
        """Register a function as a task; usable as `@app.task` or `@app.task(...)`.

        Why:
            Python calls a decorator differently in the two forms:
            `@app.task` calls task(func) directly, while `@app.task(timeout_s=5)` first calls
            task(timeout_s=5) and then calls the *returned* function with func. Checking
            whether `func` was given tells the two apart (docs/python/decorators.md).

        Args:
            func: the function (only in the bare `@app.task` form).
            name: the task name; defaults to the function's name.
            timeout_s: per-task timeout (> 0).
            max_attempts: per-task attempt budget (≥ 1).
            retry_on: which failures may be retried: worker_lost, timeout, exception.

        Returns:
            The original function unchanged (bare form), or a decorator (form with options).

        Raises:
            ValueError: invalid name, duplicate name, or invalid option.

        Example:
            >>> app = App("demo")
            >>> @app.task
            ... def add(a, b):
            ...     return a + b
            >>> add(2, 3), sorted(app.tasks())
            (5, ['add'])
        """
        # PY: the "/" makes func positional-only and the "*" makes the rest keyword-only;
        # see docs/python/functions.md.
        options = _check_options(timeout_s, max_attempts, retry_on)

        def register(target: Callable[..., object]) -> Callable[..., object]:
            """Record `target` under its name and hand it back unchanged.

            Why:
                Both decorator forms end up here, so the name rules and the duplicate check live in
                one place.

            Args:
                target: the decorated function.

            Returns:
                The same function, so it can still be called directly in tests.
            """
            task_name = name if name is not None else target.__name__
            if not TASK_NAME.match(task_name):
                raise ValueError(f"invalid task name {task_name!r} (allowed: {TASK_NAME.pattern})")
            if task_name in self._tasks:
                raise ValueError(f"task {task_name!r} is registered twice")
            self._tasks[task_name] = TaskSpec(
                name=task_name,
                func=target,
                timeout_s=options[0],
                max_attempts=options[1],
                retry_on=options[2],
                is_async=inspect.iscoroutinefunction(target),
            )
            return target

        if func is not None:
            return register(func)
        return register

    def get(self, name: str) -> TaskSpec:
        """Look up a task by name.

        Args:
            name: the task name.

        Returns:
            Its TaskSpec.

        Raises:
            KeyError: no such task (the worker reports error kind unknown_task).
        """
        return self._tasks[name]

    def tasks(self) -> Mapping[str, TaskSpec]:
        """Return every registered task, by name.

        Returns:
            A read-only view of the registry.
        """
        return dict(self._tasks)

    def manifest(self, jobs: JobsConfig) -> dict[str, object]:
        """Describe the tasks for the master, with config defaults filled in (spec/02 §4.1).

        Why:
            The master validates submits and computes timeouts from this, without ever
            importing or running the user's code (ADR-013).

        Args:
            jobs: the [jobs] config section, for default timeout and attempts.

        Returns:
            {"tasks": {name: {"timeout_s", "max_attempts", "retry_on", "is_async"}}}.
        """
        tasks: dict[str, object] = {}
        for task_name, spec in sorted(self._tasks.items()):
            retry_on = spec.retry_on if spec.retry_on is not None else DEFAULT_RETRY_ON
            tasks[task_name] = {
                "timeout_s": spec.timeout_s
                if spec.timeout_s is not None
                else jobs.default_timeout_s,
                "max_attempts": (
                    spec.max_attempts
                    if spec.max_attempts is not None
                    else jobs.default_max_attempts
                ),
                "retry_on": sorted(retry_on),
                "is_async": spec.is_async,
            }
        return {"tasks": tasks}


def _check_options(
    timeout_s: float | None, max_attempts: int | None, retry_on: Sequence[str] | None
) -> tuple[float | None, int | None, frozenset[str] | None]:
    """Validate a task's options at registration time (import time), not at run time.

    Why:
        A typo like retry_on=["timout"] should fail the moment the app is imported, where the
        traceback points at the decorator, not hours later when a job fails.

    Args:
        timeout_s: the timeout option.
        max_attempts: the attempt budget.
        retry_on: the retryable failure kinds.

    Returns:
        The cleaned (timeout_s, max_attempts, retry_on).

    Raises:
        ValueError: on any invalid option.
    """
    if timeout_s is not None and (isinstance(timeout_s, bool) or timeout_s <= 0):
        raise ValueError("timeout_s must be a number > 0")
    if max_attempts is not None and (
        isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1
    ):
        raise ValueError("max_attempts must be an integer ≥ 1")
    kinds = None
    if retry_on is not None:
        kinds = frozenset(retry_on)
        unknown = kinds - RETRY_KINDS
        if unknown:
            raise ValueError(
                f"unknown retry_on kinds {sorted(unknown)}; allowed: {sorted(RETRY_KINDS)}"
            )
    return timeout_s, max_attempts, kinds


def load_app(spec: str, pythonpath: Sequence[str] = ()) -> App:
    """Import "package.module:attribute" and return the App it names.

    Why:
        The same convention as gunicorn's "module:app". Every failure becomes an
        AppLoadError that says exactly what to fix, because a worker can only report it.

    Args:
        spec: such as "examples.demo:app".
        pythonpath: folders to put at the front of sys.path first (config app.pythonpath).

    Returns:
        The App.

    Raises:
        AppLoadError: bad spec format, import failure, missing attribute, or not an App.
    """
    module_name, sep, attribute = spec.partition(":")
    if not sep or not module_name or not attribute:
        raise AppLoadError(f"app spec {spec!r} must look like 'package.module:attribute'")
    for folder in reversed(list(pythonpath)):
        if folder not in sys.path:
            sys.path.insert(0, folder)
    try:
        # PY: importlib.import_module("a.b") does the same as "import a.b", with the name as
        # a string. See docs/python/importlib.md.
        module = importlib.import_module(module_name)
    except (
        Exception
    ) as error:  # WHY: any error in user code at import time is a boot failure (spec/04 §5.1)
        raise AppLoadError(
            f"importing {module_name!r} failed: {type(error).__name__}: {error}"
        ) from error
    app = getattr(module, attribute, None)
    if app is None:
        raise AppLoadError(f"module {module_name!r} has no attribute {attribute!r}")
    if not isinstance(app, App):
        raise AppLoadError(f"{spec!r} is a {type(app).__name__}, not a kiln.App")
    return app
