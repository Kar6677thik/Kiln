"""Each worker.gc_mode really takes effect in workers, for every start method (AC-3.2).

The expected state is the table of K3 spec §5.5, observed from inside a worker with the
`labs.gc_stats` task.
"""

from collections.abc import Callable

import pytest
from kiln_helpers import KilnServer

pytestmark = pytest.mark.integration
Start = Callable[..., KilnServer]
FAST = {"KILN_WORKER__HEARTBEAT_INTERVAL_S": "0.2"}


def gc_stats(server: KilnServer) -> dict[str, object]:
    """Ask a worker for its GC state.

    Args:
        server: the server.

    Returns:
        The labs.gc_stats result.
    """
    client = server.client()
    job = client.wait(client.submit("labs.gc_stats"), timeout_s=15)["job"]
    assert isinstance(job, dict)
    result = job["result"]
    assert isinstance(result, dict)
    return result


@pytest.mark.parametrize("mode", ["default", "disabled", "threshold0", "freeze"])
def test_worker_gc_state_matches_the_mode(kiln_server: Start, mode: str, start_method: str) -> None:
    """default: on; disabled: off; threshold0: on with threshold 0; freeze: on, objects frozen.

    Under spawn nothing is inherited, so freeze has nothing to freeze (and the master warns).
    """
    server = kiln_server("--workers", "1", env={**FAST, "KILN_WORKER__GC_MODE": mode})
    stats = gc_stats(server)
    threshold = stats["threshold"]
    assert isinstance(threshold, list)
    frozen = int(str(stats["frozen"]))
    if mode == "default":
        assert stats["enabled"] is True
        assert threshold[0] > 0
    elif mode == "disabled":
        assert stats["enabled"] is False
    elif mode == "threshold0":
        assert stats["enabled"] is True
        assert threshold[0] == 0
    else:
        assert stats["enabled"] is True
        if start_method == "spawn":
            assert frozen == 0
            assert server.events("gc_mode_ignored")
        else:
            assert frozen > 0


def test_explicit_collect_runs_while_disabled(kiln_server: Start) -> None:
    """Gate G3.4: labs.gc_collect works even with gc_mode=disabled (the GC stays off)."""
    server = kiln_server("--workers", "1", env={**FAST, "KILN_WORKER__GC_MODE": "disabled"})
    client = server.client()
    job = client.wait(client.submit("labs.gc_collect", kwargs={"generation": 2}), timeout_s=15)
    result = job["job"]["result"]  # type: ignore[index]
    assert isinstance(result, dict)
    assert result["ms"] >= 0
    assert gc_stats(server)["enabled"] is False


def test_threshold0_means_no_automatic_collections(kiln_server: Start) -> None:
    """Gate G3.2: under threshold0, churn creates garbage but no collection runs by itself."""
    server = kiln_server("--workers", "1", env={**FAST, "KILN_WORKER__GC_MODE": "threshold0"})
    before = gc_stats(server)["stats"]
    client = server.client()
    client.wait(client.submit("labs.churn", kwargs={"n": 200_000}), timeout_s=30)
    after = gc_stats(server)["stats"]
    assert isinstance(before, list)
    assert isinstance(after, list)
    runs = [a["collections"] - b["collections"] for a, b in zip(after, before, strict=True)]
    assert runs == [0, 0, 0]
