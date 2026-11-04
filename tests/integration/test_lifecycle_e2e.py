"""Reload, scaling, recycling, timeouts, heartbeats and shutdown (docs/spec/04-supervisor.md §5)."""

import os
import signal
import threading
import time
from collections.abc import Callable

import pytest
from kiln_helpers import KilnServer, wait_for

from kiln.client import KilnClient

pytestmark = pytest.mark.integration
FAST = {
    "KILN_WORKER__HEARTBEAT_INTERVAL_S": "0.2",
    "KILN_WORKER__HEARTBEAT_TIMEOUT_S": "3",
    "KILN_MASTER__GRACEFUL_TIMEOUT_S": "5",
}
Start = Callable[..., KilnServer]


def run_jobs(server: KilnServer, count: int, ms: int, results: list[str]) -> None:
    """Submit `count` sleep jobs from one client and record each final state.

    Args:
        server: the server.
        count: how many jobs.
        ms: each job's sleep.
        results: final states are appended here.
    """
    client = server.client()
    ids = [client.submit("labs.sleep", kwargs={"ms": ms}) for _ in range(count)]
    for job_id in ids:
        job = client.wait(job_id, timeout_s=120)["job"]
        assert isinstance(job, dict)
        results.append(str(job["state"]))
    client.close()


def test_reload_under_load_drops_nothing(kiln_server: Start) -> None:
    """AC-1.6 (SC-2, small): 2,000 jobs with 3 reloads in the middle all SUCCEED.

    The queue lives in the master, and retiring workers finish their in-flight jobs
    (ADR-007), so replacing every worker must not lose a single job.
    """
    server = kiln_server("--workers", "4", env=FAST)
    results: list[str] = []
    threads = [threading.Thread(target=run_jobs, args=(server, 500, 2, results)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for _ in range(3):
        time.sleep(0.5)
        server.signal(signal.SIGHUP)
    for thread in threads:
        thread.join(timeout=120)
    assert len(results) == 2000
    assert set(results) == {"SUCCEEDED"}
    wait_for(lambda: len(server.events("reload_finished")) >= 1, what="reload_finished")


def test_scale_up_and_down(kiln_server: Start) -> None:
    """SIGTTIN adds a worker, SIGTTOU retires the newest one (spec/04 §3)."""
    server = kiln_server("--workers", "2", env=FAST)
    client = server.client()
    server.signal(signal.SIGTTIN)
    wait_for(lambda: len(client.stats()["workers"]) == 3, what="3 workers")  # type: ignore[arg-type]
    server.signal(signal.SIGTTOU)
    wait_for(lambda: len(client.stats()["workers"]) == 2, what="back to 2 workers")  # type: ignore[arg-type]
    assert server.events("scale_changed")[-1]["new"] == "2"


def test_max_jobs_with_jitter_recycles_between_10_and_15(kiln_server: Start) -> None:
    """AC-1.8: with max_jobs=10 and jitter 5, each worker retires after 10–15 jobs."""
    server = kiln_server(
        "--workers",
        "1",
        env={**FAST, "KILN_WORKER__MAX_JOBS": "10", "KILN_WORKER__MAX_JOBS_JITTER": "5"},
    )
    results: list[str] = []
    run_jobs(server, 40, 1, results)
    assert set(results) == {"SUCCEEDED"}
    retired = wait_for(
        lambda: [e for e in server.events("worker_died") if e["how"] == "retired"],
        what="a retirement",
    )
    assert isinstance(retired, list)
    for death in retired:
        assert 10 <= int(death["jobs_done"]) <= 15


def test_max_rss_retires_gracefully(kiln_server: Start) -> None:
    """AC-1.8: a worker over max_rss_mb retires (exit 0), it is not killed."""
    server = kiln_server("--workers", "1", env={**FAST, "KILN_WORKER__MAX_RSS_MB": "200"})
    client = server.client()
    job = client.wait(client.submit("labs.alloc", kwargs={"mb": 300, "hold": True}), timeout_s=20)[
        "job"
    ]
    assert isinstance(job, dict)
    assert job["state"] == "SUCCEEDED"
    death = wait_for(lambda: server.events("worker_died"), what="the worker to retire")
    assert isinstance(death, list)
    assert death[0]["how"] == "retired"
    assert server.events("worker_retiring")[0]["reason"] == "max_rss"


def test_job_timeout_kills_the_worker_and_retries_once(kiln_server: Start) -> None:
    """AC-1.7: a 5 s job with timeout_s=1 is TIMED_OUT within ~1.5 s; with 2 attempts it retries.

    demo.slow has max_attempts=2 and retry_on=["timeout"], so it is tried twice, then TIMED_OUT.
    """
    server = kiln_server("--workers", "1", env=FAST)
    client = server.client()
    started = time.monotonic()
    one = client.wait(
        client.submit("labs.sleep", kwargs={"ms": 5000}, timeout_s=1.0), timeout_s=20
    )["job"]
    assert isinstance(one, dict)
    assert one["state"] == "TIMED_OUT"
    assert time.monotonic() - started < 2.5
    two = client.wait(client.submit("slow", [5], timeout_s=1.0), timeout_s=30)["job"]
    assert isinstance(two, dict)
    assert two["state"] == "TIMED_OUT"
    assert len(two["attempts"]) == 2  # type: ignore[arg-type]
    kills = [e for e in server.events("worker_kill_sent") if e["reason"] == "job_timeout"]
    assert len(kills) == 3


def test_gil_holding_worker_is_killed_by_heartbeat_timeout(kiln_server: Start) -> None:
    """A worker stuck in C code holding the GIL stops heartbeating and gets killed (spec/04 §5.4).

    On a free-threaded build there's no GIL to hold, so the heartbeat keeps going (K1 lab d).
    """
    probe = getattr(__import__("sys"), "_is_gil_enabled", None)
    if probe is not None and not probe():
        pytest.skip("free-threaded build: holding the GIL doesn't stop other threads")
    server = kiln_server("--workers", "1", env={**FAST, "KILN_WORKER__HEARTBEAT_TIMEOUT_S": "2"})
    client = server.client()
    job = client.wait(client.submit("labs.hold_gil", kwargs={"seconds": 8}), timeout_s=20)["job"]
    assert isinstance(job, dict)
    assert job["state"] == "LOST"
    assert server.events("worker_heartbeat_timeout")


def test_cpu_loop_keeps_heartbeating(kiln_server: Start) -> None:
    """A pure-Python CPU loop does NOT stop heartbeats: the interpreter switches threads."""
    server = kiln_server("--workers", "1", env={**FAST, "KILN_WORKER__HEARTBEAT_TIMEOUT_S": "1"})
    client = server.client()
    job = client.wait(client.submit("labs.burn_cpu", kwargs={"ms": 3000}), timeout_s=20)["job"]
    assert isinstance(job, dict)
    assert job["state"] == "SUCCEEDED"
    assert not server.events("worker_heartbeat_timeout")


def test_graceful_shutdown_cancels_queue_and_finishes_running(kiln_server: Start) -> None:
    """AC-1.9: SIGTERM cancels QUEUED jobs (waiters are woken), lets RUNNING finish, exits 0."""
    server = kiln_server("--workers", "1", env=FAST)
    client = server.client()
    running = client.submit("labs.sleep", kwargs={"ms": 800})
    queued = client.submit("labs.sleep", kwargs={"ms": 10})
    time.sleep(0.2)
    waiter = KilnClient("127.0.0.1", server.port)
    waiter.connect()
    server.signal(signal.SIGTERM)
    assert waiter.wait(queued, timeout_s=10)["job"]["state"] == "CANCELLED"  # type: ignore[index]
    assert client.wait(running, timeout_s=10)["job"]["state"] == "SUCCEEDED"  # type: ignore[index]
    assert server.wait_exit(timeout=15) == 0


def test_double_ctrl_c_exits_fast_and_workers_ignore_sigint(kiln_server: Start) -> None:
    """AC-1.9: Ctrl-C twice: workers ignore SIGINT, the master exits 1 within 3 s.

    The first SIGINT goes to the whole process group, as Ctrl-C does: workers ignore it and
    the master starts a graceful shutdown. The second SIGINT means "fast".
    """
    server = kiln_server("--workers", "2", env=FAST)
    client = server.client()
    client.submit("labs.sleep", kwargs={"ms": 10000})
    time.sleep(0.2)
    os.killpg(server.proc.pid, signal.SIGINT)
    time.sleep(0.3)
    assert not [e for e in server.events("worker_died") if e.get("signal") == "SIGINT"]
    started = time.monotonic()
    server.signal(signal.SIGINT)
    assert server.wait_exit(timeout=10) == 1
    assert time.monotonic() - started < 3.0


def test_sigquit_exits_immediately(kiln_server: Start) -> None:
    """AC-1.9: SIGQUIT kills every worker and exits 1 right away."""
    server = kiln_server("--workers", "2", env=FAST)
    server.signal(signal.SIGQUIT)
    assert server.wait_exit(timeout=5) == 1


def test_pidfile_survives_worker_deaths(kiln_server: Start) -> None:
    """A dying forked worker must not delete the master's pidfile (ADR-028, os._exit)."""
    server = kiln_server("--workers", "1", env=FAST)
    client = server.client()
    client.wait(client.submit("labs.crash", kwargs={"how": "exit1"}), timeout_s=10)
    assert server.pidfile.read_text().strip() == str(server.proc.pid)
