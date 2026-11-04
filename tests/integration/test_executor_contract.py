"""The shared contract every executor must meet (K4 AC-4.1), run against a real server.

Each test runs once per executor (and per start method, through the `start_method` fixture
of tests/support/kiln_helpers.py). `interpreters` needs Python 3.14+ and is skipped below.
"""

import signal
import sys
import time
from collections.abc import Callable

import pytest
from kiln_helpers import KilnServer

pytestmark = pytest.mark.integration
Start = Callable[..., KilnServer]
EXECUTORS = ["sync", "threads", "asyncio", "interpreters"]


@pytest.fixture(params=EXECUTORS)
def executor(request: pytest.FixtureRequest) -> str:
    """The executor under test (interpreters only on 3.14+).

    Returns:
        Its name.
    """
    name = str(request.param)
    if name == "interpreters" and sys.version_info < (3, 14):
        pytest.skip("the interpreters executor needs Python 3.14+")
    return name


@pytest.fixture(params=["fork"])
def start_method(request: pytest.FixtureRequest) -> str:
    """Executors don't depend on the start method; fork keeps this suite fast.

    Returns:
        "fork".
    """
    return str(request.param)


def serve(kiln_server: Start, executor: str, concurrency: int = 4) -> KilnServer:
    """Start one worker with the given executor.

    Args:
        kiln_server: the fixture.
        executor: worker.executor.
        concurrency: worker.executor_concurrency.

    Returns:
        The server.
    """
    env = {
        "KILN_WORKER__EXECUTOR": executor,
        "KILN_WORKER__EXECUTOR_CONCURRENCY": str(concurrency),
        "KILN_WORKER__HEARTBEAT_INTERVAL_S": "0.2",
    }
    return kiln_server("--workers", "1", env=env)


def run(server: KilnServer, task: str, **kwargs: object) -> dict[str, object]:
    """Submit one job and wait for it.

    Args:
        server: the server.
        task: the task name.
        **kwargs: the task's keyword arguments.

    Returns:
        The finished job.
    """
    client = server.client()
    job = client.wait(client.submit(task, kwargs=kwargs), timeout_s=30)["job"]
    assert isinstance(job, dict)
    return job


def test_result_comes_back(kiln_server: Start, executor: str) -> None:
    """A normal task returns its value."""
    server = serve(kiln_server, executor)
    assert run(server, "labs.echo", value={"a": [1, 2]})["result"] == {"a": [1, 2]}


def test_exception_becomes_failed(kiln_server: Start, executor: str) -> None:
    """A task that raises ends FAILED with the exception's type, and the worker survives."""
    server = serve(kiln_server, executor)
    job = run(server, "labs.raise", message="boom")
    assert job["state"] == "FAILED"
    assert job["error"]["type"] == "RuntimeError"  # type: ignore[index]
    assert run(server, "labs.echo", value=1)["result"] == 1


def test_non_json_result_is_a_serialization_error(kiln_server: Start, executor: str) -> None:
    """NaN can't travel as JSON: error kind serialization (ADR-009)."""
    job = run(serve(kiln_server, executor), "labs.nan")
    assert job["state"] == "FAILED"
    assert job["error"]["kind"] == "serialization"  # type: ignore[index]


def test_async_task_runs(kiln_server: Start, executor: str) -> None:
    """An `async def` task works under every executor (K4 §5.3–§5.6, decision D4-1)."""
    job = run(serve(kiln_server, executor), "labs.async_sleep", ms=50)
    assert job["state"] == "SUCCEEDED"
    assert float(str(job["result"])) >= 45


def test_capacity_is_respected_and_used(kiln_server: Start, executor: str) -> None:
    """With capacity C, C jobs run at once (except sync: 1), and never more.

    Eight 400 ms sleeps on one worker: capacity 4 needs about 2 rounds (~0.8 s), capacity 1
    needs 8 rounds (~3.2 s).
    """
    server = serve(kiln_server, executor, concurrency=4)
    client = server.client()
    started = time.monotonic()
    ids = [client.submit("labs.sleep", kwargs={"ms": 400}) for _ in range(8)]
    max_inflight = 0
    while time.monotonic() - started < 10:
        workers = client.stats()["workers"]
        assert isinstance(workers, list)
        max_inflight = max(max_inflight, int(workers[0]["inflight"]))
        if all(client.status(i)["state"] == "SUCCEEDED" for i in ids):
            break
        time.sleep(0.05)
    elapsed = time.monotonic() - started
    capacity = 1 if executor == "sync" else 4
    assert max_inflight <= capacity
    if executor == "sync":
        assert elapsed >= 3.0
    else:
        assert elapsed < 2.4


def test_graceful_shutdown_finishes_in_flight_jobs(kiln_server: Start, executor: str) -> None:
    """SIGTERM to the master: every job already running on the worker still succeeds.

    We submit exactly as many jobs as the worker can run at once (1 under sync, 2 otherwise),
    so none is still queued (queued jobs are cancelled by a shutdown, spec/04 §5.6).
    """
    server = serve(kiln_server, executor)
    client = server.client()
    count = 1 if executor == "sync" else 2
    ids = [client.submit("labs.sleep", kwargs={"ms": 600}) for _ in range(count)]
    time.sleep(0.2)
    server.signal(signal.SIGTERM)
    states = [client.wait(i, timeout_s=10)["job"]["state"] for i in ids]  # type: ignore[index]
    assert states == ["SUCCEEDED"] * count
    assert server.wait_exit() == 0


def test_ready_reports_the_executor(kiln_server: Start, executor: str) -> None:
    """The worker's `ready` names its executor; the master logs it."""
    server = serve(kiln_server, executor)
    run(server, "labs.echo", value=1)
    assert server.events("worker_ready")[0]["executor"] == executor
