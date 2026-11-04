"""Integration-test helpers (imported by tests/integration/conftest.py): start a real `kiln serve`, talk to it, read its log, clean up.

WHY THIS MODULE EXISTS
    Integration tests run the real master and real forked workers as subprocesses. The rules of
    docs/spec/09-testing-strategy.md §4 apply: bind to port 0 (never collide), wait for
    *conditions* (a log line, a reply) instead of sleeping, and ALWAYS kill the whole process
    group at the end so no stray worker survives a failing test.

HOW IT FITS
    test ──kiln_server(...)──▶ KilnServer.start() ──subprocess──▶ python -m kiln serve ...
         ──server.client()──▶ KilnClient on the port the master logged
         ──server.events("worker_died")──▶ parsed log lines

READ FIRST
    docs/spec/09-testing-strategy.md §4, docs/concepts/what-is-a-process.md

MILESTONE
    Introduced in K1.
"""

import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from kiln.client import KilnClient

REPO_ROOT = Path(__file__).resolve().parents[2]  # tests/support/ → repo root
FIELD = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')


def parse_line(line: str) -> dict[str, str]:
    """Parse one `key=value` log line into a dict of strings (quotes removed).

    Args:
        line: a log line.

    Returns:
        The fields.
    """
    fields = {}
    for key, value in FIELD.findall(line):
        if value.startswith('"'):
            value = value[1:-1].replace('\\"', '"').replace("\\n", "\n")
        fields[key] = value
    return fields


def wait_for(
    condition: Callable[[], object], timeout: float = 15.0, what: str = "condition"
) -> object:
    """Poll `condition` every 20 ms until it returns something truthy (spec/09 §4.1).

    Args:
        condition: a function to poll.
        timeout: give up after this many seconds.
        what: description for the failure message.

    Returns:
        The truthy value.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


class KilnServer:
    """One `kiln serve` subprocess in its own process group.

    Why:
        start_new_session=True puts the master and its workers in a new process group, so a
        test can signal the whole group (like Ctrl-C does) and the teardown can kill it all.
    """

    def __init__(self, tmp_path: Path, args: list[str], env: dict[str, str], app: str) -> None:
        """Start the master.

        Args:
            tmp_path: folder for the log and pidfile.
            args: extra `kiln serve` arguments.
            env: extra environment variables (KILN_* settings).
            app: the app spec to serve.
        """
        self.log_path = tmp_path / "kiln.log"
        self.pidfile = tmp_path / "kiln.pid"
        command = [
            sys.executable,
            "-m",
            "kiln",
            "serve",
            app,
            "--labs",
            "--bind",
            "127.0.0.1:0",
            "--pidfile",
            str(self.pidfile),
            *args,
        ]
        full_env = {**os.environ, "KILN_LOG__LEVEL": "DEBUG", **env}
        self._log_file = self.log_path.open("w")
        self.proc = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            stderr=self._log_file,
            stdout=subprocess.DEVNULL,
            env=full_env,
            start_new_session=True,
        )
        self.port = 0

    def log(self) -> str:
        """Return the master's log so far (all processes write to it).

        Returns:
            The text.
        """
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    def events(self, name: str) -> list[dict[str, str]]:
        """All log events with this event name.

        Args:
            name: such as "worker_died".

        Returns:
            Parsed lines.
        """
        return [
            parse_line(line) for line in self.log().splitlines() if f"event={name} " in line + " "
        ]

    def wait_listening(self, timeout: float = 15.0) -> int:
        """Wait for the `listening` event and remember the port.

        Args:
            timeout: seconds.

        Returns:
            The port.
        """
        line = wait_for(lambda: self.events("listening"), timeout, "the master to listen")
        assert isinstance(line, list)
        self.port = int(line[0]["bind"].rsplit(":", 1)[1])
        return self.port

    def client(self) -> KilnClient:
        """A connected client.

        Returns:
            The client.
        """
        if not self.port:
            self.wait_listening()
        client = KilnClient("127.0.0.1", self.port, timeout_s=60.0)
        client.connect()
        return client

    def signal(self, sig: signal.Signals) -> None:
        """Send a signal to the master only.

        Args:
            sig: the signal.
        """
        os.kill(self.proc.pid, sig)

    def wait_exit(self, timeout: float = 30.0) -> int:
        """Wait for the master to exit.

        Args:
            timeout: seconds.

        Returns:
            Its exit code.
        """
        return self.proc.wait(timeout=timeout)

    def worker_pids(self) -> list[int]:
        """Pids of workers that are READY (from the log), newest last.

        Returns:
            The pids.
        """
        return [int(e["pid_worker"]) for e in self.events("worker_ready")]

    def kill_all(self) -> None:
        """Teardown: SIGKILL the whole process group (spec/09 §4.5) and close the log."""
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        self._log_file.close()


# WHY (K2 spec §6 step 2.3): every integration test runs once per start method, so a
# behaviour that only works under fork shows up as a failing spawn/forkserver variant.
START_METHODS = ["fork", "spawn", "forkserver"]


@pytest.fixture(params=START_METHODS)
def start_method(request: pytest.FixtureRequest) -> str:
    """The start method this test variant runs with.

    Args:
        request: pytest's request object (holds the current parameter).

    Returns:
        "fork", "spawn" or "forkserver".
    """
    return str(request.param)


@pytest.fixture
def kiln_server(tmp_path: Path, start_method: str) -> Iterator[Callable[..., KilnServer]]:
    """Factory fixture that starts servers, for example with --workers 2 and some env vars.

    Args:
        tmp_path: pytest's temporary folder.
        start_method: the start.method every server uses (from the `start_method` fixture).

    Yields:
        A function that starts servers; every server is killed after the test.
    """
    started: list[KilnServer] = []

    def start(
        *args: str,
        env: dict[str, str] | None = None,
        wait: bool = True,
        app: str = "examples.demo:app",
    ) -> KilnServer:
        """Start one server.

        Args:
            *args: extra kiln serve arguments.
            env: extra environment variables.
            wait: wait until it is listening.
            app: the app spec.

        Returns:
            The server.
        """
        folder = tmp_path / f"server{len(started)}"
        folder.mkdir()
        server = KilnServer(
            folder, list(args), {"KILN_START__METHOD": start_method, **(env or {})}, app
        )
        started.append(server)
        if wait:
            server.wait_listening()
        return server

    yield start
    for server in started:
        server.kill_all()
