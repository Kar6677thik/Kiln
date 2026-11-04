"""End-to-end tests of the master: real `kiln serve`, real forked workers, real sockets."""

import signal
import socket
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from kiln_helpers import KilnServer, wait_for

from kiln.client import KilnClient
from kiln.errors import KilnError
from kiln.protocol.framing import FrameDecoder, encode_frame

pytestmark = pytest.mark.integration
FAST = {
    "KILN_WORKER__HEARTBEAT_INTERVAL_S": "0.2",
    "KILN_WORKER__HEARTBEAT_TIMEOUT_S": "3",
    "KILN_MASTER__GRACEFUL_TIMEOUT_S": "5",
}
Start = Callable[..., KilnServer]


def test_submit_then_wait_returns_the_result(kiln_server: Start) -> None:
    """AC-1.3: submit → wait gives SUCCEEDED with the task's return value."""
    client = kiln_server("--workers", "2", env=FAST).client()
    job_id = client.submit("add", [2, 3])
    job = client.wait(job_id, timeout_s=10)["job"]
    assert isinstance(job, dict)
    assert job["state"] == "SUCCEEDED"
    assert job["result"] == 5


def test_pipelined_requests_are_matched_by_req_id(kiln_server: Start) -> None:
    """AC-1.3: requests sent back to back are answered with the right req_ids, out of order.

    A `wait` on a slow job is sent first and a `ping` second; the ping's reply arrives first.
    """
    server = kiln_server("--workers", "2", env=FAST)
    client = server.client()
    slow = client.submit("labs.sleep", kwargs={"ms": 500})
    sock = socket.create_connection(("127.0.0.1", server.port))
    sock.sendall(
        encode_frame({"type": "wait", "req_id": 1, "job_id": slow, "timeout_s": 10})
        + encode_frame({"type": "ping", "req_id": 2})
    )
    decoder = FrameDecoder()
    replies: list[dict[str, object]] = []
    while len(replies) < 2:
        replies += decoder.feed(sock.recv(65536))
    sock.close()
    assert [r["req_id"] for r in replies] == [2, 1]


def test_unknown_job_is_not_found(kiln_server: Start) -> None:
    """AC-1.3: status of an unknown id gives NOT_FOUND."""
    client = kiln_server(env=FAST).client()
    with pytest.raises(KilnError, match="NOT_FOUND"):
        client.status("0" * 32)


def test_queue_full_when_the_queue_limit_is_reached(kiln_server: Start) -> None:
    """AC-1.3: with queue.max_size=3 and one busy worker, the 5th submit is QUEUE_FULL."""
    client = kiln_server("--workers", "1", env={**FAST, "KILN_QUEUE__MAX_SIZE": "3"}).client()
    client.submit("labs.sleep", kwargs={"ms": 2000})
    time.sleep(0.2)  # WHY: let the first job be dispatched so it leaves the queue
    for _ in range(3):
        client.submit("labs.sleep", kwargs={"ms": 10})
    with pytest.raises(KilnError, match="QUEUE_FULL"):
        client.submit("labs.sleep", kwargs={"ms": 10})


def test_task_exception_fails_the_job_with_the_traceback(kiln_server: Start) -> None:
    """A task that raises makes the job FAILED with error kind "exception" and a traceback."""
    client = kiln_server(env=FAST).client()
    job = client.wait(client.submit("labs.raise", ["boom"]), timeout_s=10)["job"]
    assert isinstance(job, dict)
    assert job["state"] == "FAILED"
    assert job["error"]["kind"] == "exception"  # type: ignore[index]
    assert "boom" in job["error"]["traceback"]  # type: ignore[index]


def test_worker_fds_are_only_its_own(kiln_server: Start) -> None:
    """AC-1.14: a forked worker holds none of the master's sockets (spec/04 §6 rule 1).

    We compare the socket inodes in /proc/<pid>/fd: the master's listener must not appear in
    any worker, and a worker must hold exactly one socket from the master's side: its channel.
    """
    server = kiln_server("--workers", "2", env=FAST)
    server.client().ping()
    listener_fd = int(server.events("listening")[0]["fd"])
    master_listener = str(Path(f"/proc/{server.proc.pid}/fd/{listener_fd}").readlink())
    assert master_listener.startswith("socket:")
    for pid in server.worker_pids():
        targets = []
        for entry in Path(f"/proc/{pid}/fd").iterdir():
            try:
                targets.append(str(entry.readlink()))
            except FileNotFoundError:
                continue  # WHY: an fd closed between listing and reading it
        assert master_listener not in targets
        sockets = [t for t in targets if t.startswith("socket:")]
        assert len(sockets) == 1, targets


def test_master_survives_a_client_sending_garbage(kiln_server: Start) -> None:
    """A client sending an invalid frame gets BAD_FRAME and is disconnected; others carry on."""
    server = kiln_server(env=FAST)
    bad = socket.create_connection(("127.0.0.1", server.port))
    bad.sendall(b"\x00\x00\x00\x05nope!")
    reply = FrameDecoder().feed(bad.recv(65536))
    assert reply[0]["code"] == "BAD_FRAME"
    assert bad.recv(1) == b""
    bad.close()
    assert server.client().ping()["pong"] is True


def test_sigusr1_logs_a_status_dump(kiln_server: Start) -> None:
    """SIGUSR1 makes the master log a one-line status dump (spec/04 §3)."""
    server = kiln_server(env=FAST)
    server.signal(signal.SIGUSR1)
    wait_for(lambda: server.events("status_dump"), what="status_dump")


def test_client_retries_until_the_master_listens(kiln_server: Start) -> None:
    """The listener opens only after the first READY (ADR-013); connect() retries meanwhile."""
    server = kiln_server(env=FAST)
    client = KilnClient("127.0.0.1", server.port)
    client.connect(retry_for_s=5)
    assert client.ping()["pong"] is True
