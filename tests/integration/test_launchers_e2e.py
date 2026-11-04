"""What each start method really copies (K2 spec §5.5), and the forkserver's failure handling."""

import os
import signal
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from kiln_helpers import KilnServer, wait_for

pytestmark = pytest.mark.integration
FAST = {"KILN_WORKER__HEARTBEAT_INTERVAL_S": "0.2", "KILN_WORKER__HEARTBEAT_TIMEOUT_S": "3"}
Start = Callable[..., KilnServer]

COUNTER_APP = '''"""A test app that records every process that imports it."""
import os
import kiln

with open(os.environ["KILN_TEST_IMPORT_LOG"], "a") as log_file:
    log_file.write(f"{os.getpid()}\\n")

app = kiln.App("counter")


@app.task
def ping() -> str:
    """Answer pong."""
    return "pong"
'''


def counter_app(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """Write the import-counting app and return the env that makes it importable.

    Args:
        tmp_path: a temporary folder.

    Returns:
        (environment variables, the import log file).
    """
    (tmp_path / "counter_app.py").write_text(COUNTER_APP)
    log = tmp_path / "imports.log"
    env = {"KILN_APP__PYTHONPATH": f'["{tmp_path}"]', "KILN_TEST_IMPORT_LOG": str(log)}
    return env, log


@pytest.mark.parametrize("start_method", ["fork", "spawn", "forkserver"], indirect=True)
def test_who_imports_the_app(kiln_server: Start, tmp_path: Path, start_method: str) -> None:
    """AC-2.2: the app is imported once (master or forkserver) or once per worker (spawn).

    fork: the master preloads it and workers inherit it. forkserver: the *server* preloads it,
    the master never does. spawn: every fresh interpreter imports it itself.
    """
    env, log = counter_app(tmp_path)
    server = kiln_server("--workers", "3", env={**FAST, **env}, app="counter_app:app")
    assert server.client().ping()["pong"] is True
    wait_for(lambda: len(server.worker_pids()) == 3, what="3 ready workers")
    importers = set(log.read_text().split())
    expected = {"fork": 1, "forkserver": 1, "spawn": 3}[start_method]
    assert len(importers) == expected
    assert (str(server.proc.pid) in importers) is (start_method == "fork")


def test_workers_have_different_random_state(kiln_server: Start) -> None:
    """Each worker's `random` differs from the others' (K2 spec §5.5, gate G1.3).

    The dispatcher sends work to the least-loaded worker, lowest id first, so a probe goes to
    worker 1 only while worker 0 is busy: we keep worker 0 busy with a sleep first.
    """
    client = kiln_server("--workers", "2", env=FAST).client()
    busy = client.submit("labs.sleep", kwargs={"ms": 500})
    on_worker_1 = client.wait(client.submit("labs.random_probe"), timeout_s=10)["job"]
    client.wait(busy, timeout_s=10)
    on_worker_0 = client.wait(client.submit("labs.random_probe"), timeout_s=10)["job"]
    assert isinstance(on_worker_0, dict)
    assert isinstance(on_worker_1, dict)
    ids = {on_worker_0["attempts"][0]["worker_id"], on_worker_1["attempts"][0]["worker_id"]}  # type: ignore[index]
    assert ids == {0, 1}
    assert on_worker_0["result"] != on_worker_1["result"]


def signal_mask(pid: int, field: str) -> int:
    """Read a signal bitmask (SigIgn, SigCgt) from /proc/<pid>/status.

    Args:
        pid: the process.
        field: the field name.

    Returns:
        The mask as an integer (bit N-1 is signal N).
    """
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith(field + ":"):
            return int(line.split()[1], 16)
    raise AssertionError(f"{field} not found")


def test_workers_ignore_sigint_and_share_the_process_group(kiln_server: Start) -> None:
    """Every launcher yields workers that ignore SIGINT and sit in the master's process group.

    The group matters for Ctrl-C and for test cleanup (gate G2.11); SIG_IGN for SIGINT is
    spec/04 §3.1. The kernel shows ignored signals as a bitmask in /proc/<pid>/status.
    """
    server = kiln_server("--workers", "2", env=FAST)
    wait_for(lambda: len(server.worker_pids()) == 2, what="2 workers")
    for pid in server.worker_pids():
        assert signal_mask(pid, "SigIgn") & (1 << (signal.SIGINT - 1))
        assert os.getpgid(pid) == os.getpgid(server.proc.pid)


@pytest.mark.parametrize("start_method", ["forkserver"], indirect=True)
def test_forkserver_death_orphans_workers_and_restarts(
    kiln_server: Start, start_method: str
) -> None:
    """AC-2.3: SIGKILL the forkserver: it's restarted; its old workers become pidfd-watched orphans.

    The orphans keep serving (their channels still reach the master). When one later dies,
    its exit status is unknowable, so the death is classified unknown_exit.
    """
    server = kiln_server("--workers", "2", env=FAST)
    client = server.client()
    old_workers = server.worker_pids()
    server_pid = int(wait_for(lambda: _forkserver_pid(server), what="forkserver pid"))  # type: ignore[arg-type]
    os.kill(server_pid, signal.SIGKILL)
    wait_for(lambda: server.events("forkserver_died"), what="forkserver_died")
    wait_for(lambda: len(server.events("launcher_started")) >= 2, what="forkserver restart")
    assert client.wait(client.submit("add", [1, 2]), timeout_s=10)["job"]["result"] == 3  # type: ignore[index]
    os.kill(old_workers[0], signal.SIGKILL)
    death = wait_for(
        lambda: [e for e in server.events("worker_died") if e["how"] == "unknown_exit"],
        what="orphan death",
    )
    assert isinstance(death, list)
    assert death[0]["pid_worker"] == str(old_workers[0])


def _forkserver_pid(server: KilnServer) -> int | None:
    """Find the current forkserver's pid from the master's `launcher_started` event.

    Args:
        server: the server.

    Returns:
        The pid or None.
    """
    events = server.events("launcher_started")
    return int(events[-1]["pid_launcher"]) if events else None


@pytest.mark.parametrize("start_method", ["forkserver"], indirect=True)
def test_master_death_takes_forkserver_workers_down(kiln_server: Start, start_method: str) -> None:
    """AC-2.3: if the master is SIGKILLed, the forkserver kills its workers within 6 s."""
    server = kiln_server("--workers", "2", env=FAST)
    wait_for(lambda: len(server.worker_pids()) == 2, what="2 workers")
    workers = server.worker_pids()
    os.kill(server.proc.pid, signal.SIGKILL)
    server.proc.wait(timeout=5)
    deadline = time.monotonic() + 6
    alive = workers
    while alive and time.monotonic() < deadline:
        alive = [pid for pid in workers if _alive(pid)]
        time.sleep(0.1)
    assert alive == []


def _alive(pid: int) -> bool:
    """Whether a process exists and isn't a zombie.

    Args:
        pid: the process.

    Returns:
        True if it's running.
    """
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return False
    return state != "Z"
