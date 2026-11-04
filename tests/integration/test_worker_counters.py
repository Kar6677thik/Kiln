"""The worker's own counters stay exact when several threads finish jobs at once (AC-4.5).

Meaningful on 3.15t, where pool threads really run in parallel; on GIL builds it guards
against regressions. The worker reports its own count in its last log line, worker_exiting.
"""

import signal
from collections.abc import Callable

import pytest
from kiln_helpers import KilnServer

pytestmark = pytest.mark.integration
Start = Callable[..., KilnServer]


@pytest.fixture(params=["fork"])
def start_method(request: pytest.FixtureRequest) -> str:
    """The start method doesn't matter here.

    Returns:
        "fork".
    """
    return str(request.param)


def test_jobs_done_exact_under_threads(kiln_server: Start) -> None:
    """2,000 jobs on the threads executor with concurrency 8: jobs_done is exactly 2,000."""
    env = {"KILN_WORKER__EXECUTOR": "threads", "KILN_WORKER__EXECUTOR_CONCURRENCY": "8"}
    server = kiln_server("--workers", "1", env=env)
    client = server.client()
    ids = [client.submit("labs.echo", kwargs={"value": i}) for i in range(2000)]
    for job_id in ids:
        assert client.wait(job_id, timeout_s=60)["job"]["state"] == "SUCCEEDED"  # type: ignore[index]
    server.signal(signal.SIGTERM)
    assert server.wait_exit() == 0
    exits = [e for e in server.events("worker_exiting") if e.get("role") == "worker"]
    assert [int(e["jobs_done"]) for e in exits] == [2000]
