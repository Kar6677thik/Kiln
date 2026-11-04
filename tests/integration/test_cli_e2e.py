"""The `kiln` CLI against a real server: submit, stats, reload, scale, stop, bench load."""

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from kiln_helpers import REPO_ROOT, KilnServer, wait_for

pytestmark = pytest.mark.integration
Start = Callable[..., KilnServer]


def kiln(server: KilnServer, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a `kiln` client command against the server.

    Args:
        server: the running server.
        *args: the command and its arguments.

    Returns:
        The completed process.
    """
    command = [sys.executable, "-m", "kiln", "--port", str(server.port), *args]
    return subprocess.run(
        command, capture_output=True, text=True, timeout=120, cwd=REPO_ROOT, check=False
    )


def test_submit_wait_stats_reload_scale_stop(kiln_server: Start) -> None:
    """The everyday commands work end to end and stop exits the master cleanly."""
    server = kiln_server("--workers", "1")
    done = kiln(server, "submit", "add", "--args", "[2, 3]", "--wait")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["result"] == 5
    stats = json.loads(kiln(server, "stats").stdout)
    assert stats["jobs"]["by_state"]["SUCCEEDED"] == 1
    assert kiln(server, "submit", "nope").returncode == 1
    assert kiln(server, "reload", "--pidfile", str(server.pidfile)).returncode == 0
    wait_for(lambda: server.events("reload_finished"), what="reload_finished")
    assert kiln(server, "scale", "+1", "--pidfile", str(server.pidfile)).returncode == 0
    wait_for(lambda: len(server.events("worker_ready")) >= 3, what="scaled-up worker")
    assert kiln(server, "stop", "--pidfile", str(server.pidfile)).returncode == 0
    assert server.wait_exit(timeout=15) == 0


def test_bench_load_writes_a_record(kiln_server: Start, tmp_path: Path) -> None:
    """`kiln bench load` runs, every job succeeds, and an ExperimentRecord is written."""
    server = kiln_server("--workers", "2")
    out = tmp_path / "results"
    result = kiln(
        server,
        "bench",
        "load",
        "--jobs",
        "200",
        "--concurrency",
        "8",
        "--out",
        str(out),
        "--pidfile",
        str(server.pidfile),
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["metrics"]["jobs_succeeded"] == 200
    assert Path(summary["record"]).exists()


def test_invalid_config_lists_every_problem_and_exits_2(tmp_path: Path) -> None:
    """AC-1.10: a bad config prints all problems and exits with code 2."""
    config = tmp_path / "kiln.toml"
    config.write_text("[master]\nworkers = 0\nnope = 1\n[log]\nlevel = 'LOUD'\n")
    result = subprocess.run(
        [sys.executable, "-m", "kiln", "serve", "examples.demo:app", "--config", str(config)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 2
    assert result.stderr.count("\n  - ") == 3
