"""Cooperative vs hard timeouts under the asyncio executor (K4 §5.4, AC-4.3)."""

from collections.abc import Callable

import pytest
from kiln_helpers import KilnServer, wait_for

pytestmark = pytest.mark.integration
Start = Callable[..., KilnServer]
ENV = {
    "KILN_WORKER__EXECUTOR": "asyncio",
    "KILN_WORKER__EXECUTOR_CONCURRENCY": "4",
    "KILN_JOBS__CANCEL_GRACE_S": "1.0",
    "KILN_WORKER__HEARTBEAT_INTERVAL_S": "0.2",
}


@pytest.fixture(params=["fork"])
def start_method(request: pytest.FixtureRequest) -> str:
    """The start method doesn't matter here.

    Returns:
        "fork".
    """
    return str(request.param)


def test_async_task_past_its_timeout_is_cancelled_without_a_kill(kiln_server: Start) -> None:
    """asyncio.timeout cancels the await; the job is TIMED_OUT and no worker is killed."""
    server = kiln_server("--workers", "1", env=ENV)
    client = server.client()
    job_id = client.submit("labs.async_sleep", kwargs={"ms": 5000}, timeout_s=0.5, max_attempts=1)
    job = client.wait(job_id, timeout_s=10)["job"]
    assert job["state"] == "TIMED_OUT"  # type: ignore[index]
    assert server.events("worker_kill_sent") == []
    assert (
        client.wait(client.submit("labs.echo", kwargs={"value": 7}), timeout_s=10)["job"]["result"]
        == 7
    )  # type: ignore[index]


def test_task_that_blocks_the_loop_is_hard_killed_after_the_grace(kiln_server: Start) -> None:
    """A task that blocks the loop is hard-killed, at timeout_s + cancel_grace_s.

    block_loop never awaits, so nothing can cancel it: the master kills the worker at
    0.5 + 1.0 s, not at 0.5 s.
    """
    server = kiln_server("--workers", "1", env=ENV)
    client = server.client()
    job_id = client.submit("labs.block_loop", kwargs={"ms": 6000}, timeout_s=0.5, max_attempts=1)
    kill = wait_for(lambda: server.events("worker_kill_sent"), what="the hard kill", timeout=10)
    assert isinstance(kill, list)
    assert kill[0]["reason"] == "job_timeout"
    assert client.wait(job_id, timeout_s=10)["job"]["state"] == "TIMED_OUT"  # type: ignore[index]


def test_sync_task_runs_on_a_tothread_thread(kiln_server: Start) -> None:
    """A plain `def` task under asyncio runs in the sized to_thread pool, not on the loop."""
    server = kiln_server("--workers", "1", env=ENV)
    client = server.client()
    name = client.wait(client.submit("labs.thread_name"), timeout_s=10)["job"]["result"]  # type: ignore[index]
    assert str(name).startswith("kiln-tothread")
