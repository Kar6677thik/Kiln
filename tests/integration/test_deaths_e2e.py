"""How workers die, and what Kiln says about it (docs/spec/04-supervisor.md §4, §5.1)."""

from collections.abc import Callable

import pytest
from kiln_helpers import KilnServer, wait_for

pytestmark = pytest.mark.integration
FAST = {"KILN_WORKER__HEARTBEAT_INTERVAL_S": "0.2", "KILN_WORKER__HEARTBEAT_TIMEOUT_S": "3"}
Start = Callable[..., KilnServer]


@pytest.mark.parametrize(
    ("how", "expected"),
    [
        ("exit1", {"how": "crashed", "exit_code": "1"}),
        ("segv", {"how": "killed", "signal": "SIGSEGV", "suspected_oom": "false"}),
        ("abort", {"how": "killed", "signal": "SIGABRT"}),
        (
            "sigkill_self",
            {
                "how": "killed",
                "signal": "SIGKILL",
                "suspected_oom": "true",
                "oom_confirmed": "false",
            },
        ),
    ],
)
def test_each_crash_mode_is_classified_and_the_job_is_lost(
    kiln_server: Start, how: str, expected: dict[str, str]
) -> None:
    """AC-1.4: each labs.crash mode gets its spec/04 §4.1 classification; the job becomes LOST.

    sigkill_self is the key lesson: a SIGKILL Kiln didn't send is *suspected* OOM, but the
    cgroup's oom_kill counter didn't move, so it isn't confirmed (suspected ≠ confirmed).
    """
    server = kiln_server("--workers", "1", env=FAST)
    client = server.client()
    job = client.wait(client.submit("labs.crash", kwargs={"how": how}), timeout_s=15)["job"]
    assert isinstance(job, dict)
    assert job["state"] == "LOST"
    assert job["error"]["kind"] == "worker_lost"  # type: ignore[index]
    deaths = wait_for(lambda: server.events("worker_died"), what="worker_died")
    assert isinstance(deaths, list)
    for key, value in expected.items():
        assert deaths[0][key] == value, deaths[0]
    wait_for(lambda: len(server.events("worker_ready")) >= 2, what="a replacement worker")


def test_broken_app_gives_up_with_exit_code_4(kiln_server: Start) -> None:
    """AC-1.5: an app that fails to import makes the master exit 4 after boot_failure_limit tries.

    app.preload=false makes each *worker* import the app (and exit 3), so this exercises the
    crash-loop protection: exponential backoff between attempts, then giving up.
    """
    env = {**FAST, "KILN_APP__PRELOAD": "false", "KILN_MASTER__BOOT_FAILURE_LIMIT": "3"}
    server = kiln_server("--workers", "1", env=env, wait=False, app="no_such_module:app")
    assert server.wait_exit(timeout=30) == 4
    spawned = [float(e["ts"][17:23]) for e in server.events("worker_spawned")]
    assert len(spawned) == 3
    assert server.events("boot_failure_limit_reached")


def test_preload_failure_exits_4_immediately(kiln_server: Start, start_method: str) -> None:
    """With app.preload=true (the default), a broken app is caught before any worker starts.

    Under fork the master preloads it; under forkserver the server does (and fails to start).
    Under spawn preload is ignored, so this case doesn't apply.
    """
    if start_method == "spawn":
        pytest.skip("app.preload is ignored under spawn (K2 spec §5.3)")
    server = kiln_server(env=FAST, wait=False, app="examples.nope:app")
    assert server.wait_exit(timeout=15) == 4
    assert not server.events("worker_spawned")
