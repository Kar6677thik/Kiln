"""Tests for the task API (kiln.app), lab tasks, logging format and small helpers."""

import io
import logging
import sys
from pathlib import Path

import pytest

from kiln import logsetup
from kiln.app import App, AppLoadError, load_app
from kiln.config import default_config
from kiln.labs import tasks as lab_tasks
from kiln.master.timers import TimerHeap
from kiln.memory.procfs import parse_status, read_status
from kiln.protocol.messages import Run
from kiln.worker.executors.base import run_task


def make_app() -> App:
    """Build an app with tasks registered both ways.

    Returns:
        The app.
    """
    app = App("test")

    @app.task
    def add(a: int, b: int) -> int:
        """Add.

        Args:
            a: first.
            b: second.

        Returns:
            The sum.
        """
        return a + b

    @app.task(name="custom.name", timeout_s=5, max_attempts=2, retry_on=["exception"])
    async def later() -> str:
        """An async task.

        Returns:
            "done".
        """
        return "done"

    return app


def test_both_decorator_forms_register_and_return_the_function() -> None:
    """@app.task and @app.task(...) both register, and the function stays callable (spec/03 §2)."""
    app = make_app()
    assert set(app.tasks()) == {"add", "custom.name"}
    assert app.get("add").func(2, 3) == 5
    assert app.get("custom.name").is_async


def test_manifest_fills_config_defaults() -> None:
    """The manifest the master sees has every option filled in from config defaults."""
    manifest = make_app().manifest(default_config().jobs)
    assert manifest["tasks"]["add"] == {
        "timeout_s": 30.0,
        "max_attempts": 1,
        "retry_on": ["timeout", "worker_lost"],
        "is_async": False,
    }  # type: ignore[index]
    assert manifest["tasks"]["custom.name"]["max_attempts"] == 2  # type: ignore[index]


@pytest.mark.parametrize("name", ["", "1abc", "has space", "x" * 129])
def test_invalid_task_names_are_rejected(name: str) -> None:
    """Task names follow spec/03 §2.1's pattern; errors happen at import time."""
    with pytest.raises(ValueError, match="invalid task name"):
        App("t").task(name=name)(print)


def test_duplicate_and_bad_options_are_rejected() -> None:
    """Registering a name twice, or a typo in retry_on, fails immediately."""
    app = make_app()
    with pytest.raises(ValueError, match="twice"):
        app.task(name="add")(print)
    with pytest.raises(ValueError, match="retry_on"):
        app.task(retry_on=["timout"])


def test_load_app_explains_every_failure(tmp_path: Path) -> None:
    """load_app's errors say what's wrong: bad spec, import error, missing or wrong attribute."""
    (tmp_path / "goodmod.py").write_text("import kiln\napp = kiln.App('x')\nnot_app = 5\n")
    (tmp_path / "badmod.py").write_text("raise RuntimeError('boom at import')\n")
    sys.path.insert(0, str(tmp_path))
    try:
        assert isinstance(load_app("goodmod:app"), App)
        for spec, text in [
            ("nocolon", "must look like"),
            ("badmod:app", "boom at import"),
            ("goodmod:missing", "no attribute"),
            ("goodmod:not_app", "not a kiln.App"),
        ]:
            with pytest.raises(AppLoadError, match=text):
                load_app(spec)
    finally:
        sys.path.remove(str(tmp_path))


def test_run_task_reports_values_errors_and_bad_results() -> None:
    """run_task turns success, exceptions, unknown tasks and non-JSON results into Results."""
    app = make_app()
    app.task(name="bad.result")(lambda: {1, 2})
    assert run_task(app, Run("j", 1, "add", [2, 3], {}, 30.0)).value == 5
    assert run_task(app, Run("j", 1, "custom.name", [], {}, 30.0)).value == "done"
    failed = run_task(app, Run("j", 1, "add", ["a"], {}, 30.0))
    assert not failed.ok
    assert failed.error is not None
    assert failed.error["type"] == "TypeError"
    assert run_task(app, Run("j", 1, "nope", [], {}, 30.0)).error["type"] == "UnknownTask"  # type: ignore[index]
    assert (
        run_task(app, Run("j", 1, "bad.result", [], {}, 30.0)).error["type"] == "SerializationError"
    )  # type: ignore[index]


def test_lab_tasks_register_under_labs_prefix() -> None:
    """labs.register adds the K1 lab tasks (spec/03 §7), all under "labs."."""
    app = App("x")
    lab_tasks.register(app)
    assert {"labs.echo", "labs.sleep", "labs.crash", "labs.hold_gil", "labs.alloc"} <= set(
        app.tasks()
    )
    assert lab_tasks.echo([1]) == [1]
    assert lab_tasks.burn_cpu(5) >= 1


def test_log_lines_follow_spec06_format() -> None:
    """Events become one `key=value` line with ts, level, role, pid and event (spec/06 §1)."""
    buffer = io.StringIO()
    logsetup.configure("worker", "INFO", "kv", worker_id=3, stream=buffer)
    logsetup.event(
        logging.getLogger("kiln.test"), logging.INFO, "demo", text="two words", n=1, flag=True
    )
    logsetup.event(logging.getLogger("kiln.test"), logging.DEBUG, "hidden")
    line = buffer.getvalue().strip()
    assert "\n" not in line
    assert "level=INFO role=worker" in line
    assert 'worker_id=3 event=demo text="two words" n=1 flag=true' in line


def test_json_log_format() -> None:
    """log.format = json gives one JSON object per line."""
    buffer = io.StringIO()
    logsetup.configure("master", "INFO", "json", stream=buffer)
    logsetup.event(logging.getLogger("kiln.test"), logging.WARNING, "demo", x=1)
    assert '"event": "demo"' in buffer.getvalue()


def test_timer_heap_orders_and_cancels() -> None:
    """Timers come out in deadline order, and cancelled ones never fire (lazy deletion)."""
    heap = TimerHeap()
    late = heap.schedule(3.0, "b")
    heap.schedule(1.0, "a")
    heap.cancel(late)
    heap.schedule(2.0, "c")
    assert heap.next_deadline() == 1.0
    assert [t.kind for t in heap.pop_expired(10.0)] == ["a", "c"]
    assert heap.next_deadline() is None


def test_procfs_reads_this_process() -> None:
    """read_status("self") reports a plausible RSS for the test process, in bytes."""
    info = read_status("self")
    assert info.vm_rss > 1024 * 1024
    assert parse_status("VmRSS:\t 10 kB\n").vm_rss == 10240
