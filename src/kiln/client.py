"""A small blocking KP/1 client: what the `kiln submit/status/wait/stats` commands use.

WHY THIS MODULE EXISTS
    Talking to the master means: connect, send a framed request with a req_id, read frames
    until the reply with the same req_id arrives, and turn `error` replies into exceptions.
    Doing that in one place keeps the CLI simple. Because the master only starts listening
    after its first worker is READY (ADR-013), connect() retries for a few seconds.

HOW IT FITS
    cli.py ──KilnClient(host, port).submit("add", [2, 3])──▶ TCP 127.0.0.1:7340 ──▶ master
    reply {"type":"ok",...} ──▶ dict      reply {"type":"error",...} ──▶ raise KilnError

READ FIRST
    docs/spec/02-protocol.md §3, docs/concepts/sockets-and-socketpairs.md,
    docs/python/socket-module.md

MILESTONE
    Introduced in K1.
"""

import socket
import sys
import time

from kiln.errors import ErrorCode, KilnError
from kiln.protocol.framing import FrameDecoder, encode_frame


class KilnClient:
    """One blocking connection to the master.

    Why:
        Blocking is fine for a command-line tool that does one thing and exits; the master
        is the one that must never block.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7340,
        timeout_s: float = 30.0,
        trace: bool = False,
    ) -> None:
        """Remember where the master is (call connect() before requests).

        Why:
            Nothing connects yet: connect() handles the retry while the master isn't listening
            (ADR-013).

        Args:
            host: the master's host.
            port: the master's KP/1 port.
            timeout_s: socket timeout for each read.
            trace: print every frame to stderr (ADR-008's debugging aid).
        """
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self.trace = trace
        self.sock: socket.socket | None = None
        self.decoder = FrameDecoder()
        self._next_id = 1
        self._pending: list[dict[str, object]] = []

    def connect(self, retry_for_s: float = 10.0) -> None:
        """Connect, retrying with exponential backoff while the master isn't listening yet.

        Why:
            The master opens its listener only after the first worker is READY (ADR-013), so a fresh
            server refuses connections briefly.

        Args:
            retry_for_s: how long to keep trying.

        Raises:
            KilnError: still refused after retry_for_s.
        """
        deadline = time.monotonic() + retry_for_s
        delay = 0.05
        while True:
            try:
                self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout_s)
                return
            except ConnectionRefusedError as error:
                # WHY: "connection refused" means nothing listens yet (ADR-013); wait and retry.
                if time.monotonic() + delay > deadline:
                    raise KilnError(
                        f"cannot connect to {self.host}:{self.port}: is `kiln serve` running?"
                    ) from error
                time.sleep(delay)
                delay = min(delay * 2, 1.0)

    def close(self) -> None:
        """Close the connection."""
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def _trace(self, direction: str, frame: bytes) -> None:
        """Print one frame as hex and text to stderr when --trace is on.

        Args:
            direction: ">>" (sent) or "<<" (received).
            frame: the frame bytes.
        """
        if self.trace:
            text = frame[4:].decode("utf-8", "replace")
            sys.stderr.write(f"{direction} {frame[:4].hex(' ')} | {text}\n")

    def request(self, msg: dict[str, object], timeout_s: float | None = None) -> dict[str, object]:
        """Send one request and return its `ok` reply.

        Why:
            Every client command is one request and one reply; this is the shared send, match and
            error-check path.

        Args:
            msg: the request without req_id (added here).
            timeout_s: how long to wait for the reply (default: the client's timeout).

        Returns:
            The ok reply.

        Raises:
            KilnError: an error reply (with its code), or the connection broke.
        """
        if self.sock is None:
            self.connect()
        assert self.sock is not None  # INVARIANT: connect() succeeded or raised
        req_id = self._next_id
        self._next_id += 1
        frame = encode_frame({**msg, "req_id": req_id})
        self._trace(">>", frame)
        self.sock.sendall(frame)
        reply = self._read_reply(req_id, timeout_s)
        if reply.get("type") == "error":
            code = str(reply.get("code", "INTERNAL"))
            known = code if code in ErrorCode.__members__ else "INTERNAL"
            raise KilnError(f"{code}: {reply.get('message')}", ErrorCode(known))
        return reply

    def _read_reply(self, req_id: int, timeout_s: float | None) -> dict[str, object]:
        """Read frames until the one answering req_id arrives.

        Why:
            Replies may arrive out of order (spec/02 §3), so replies for other requests are kept
            until asked for.

        Args:
            req_id: the request to wait for.
            timeout_s: read timeout.

        Returns:
            The reply.

        Raises:
            KilnError: the master closed the connection.
        """
        assert self.sock is not None  # INVARIANT: only called after sending
        self.sock.settimeout(timeout_s if timeout_s is not None else self.timeout_s)
        while True:
            for index, reply in enumerate(self._pending):
                if reply.get("req_id") == req_id:
                    return self._pending.pop(index)
            data = self.sock.recv(65536)
            if not data:
                raise KilnError("the master closed the connection")
            for reply in self.decoder.feed(data):
                self._trace("<<", encode_frame(reply))
                self._pending.append(reply)

    # --- one method per request type (spec/02 §3.1) ------------------------------------------

    def ping(self) -> dict[str, object]:
        """Health check.

        Returns:
            The reply ({"pong": true, ...}).
        """
        return self.request({"type": "ping"})

    def submit(
        self,
        task: str,
        args: list[object] | None = None,
        kwargs: dict[str, object] | None = None,
        timeout_s: float | None = None,
        max_attempts: int | None = None,
    ) -> str:
        """Queue a job.

        Why:
            The most common request; optional overrides are only sent when given, so the task's
            defaults apply otherwise.

        Args:
            task: the task name.
            args: positional arguments.
            kwargs: keyword arguments.
            timeout_s: per-job timeout override.
            max_attempts: per-job attempts override.

        Returns:
            The new job id.
        """
        msg: dict[str, object] = {
            "type": "submit",
            "task": task,
            "args": args or [],
            "kwargs": kwargs or {},
        }
        if timeout_s is not None:
            msg["timeout_s"] = timeout_s
        if max_attempts is not None:
            msg["max_attempts"] = max_attempts
        return str(self.request(msg)["job_id"])

    def status(self, job_id: str) -> dict[str, object]:
        """Get a job's JobView now.

        Args:
            job_id: the job.

        Returns:
            The JobView.
        """
        job = self.request({"type": "status", "job_id": job_id})["job"]
        return job if isinstance(job, dict) else {}

    def wait(self, job_id: str, timeout_s: float = 30.0) -> dict[str, object]:
        """Wait until the job is finished (or timeout_s passes).

        Args:
            job_id: the job.
            timeout_s: the server-side wait timeout (≤ 300).

        Returns:
            The reply: {"job": JobView} plus "timed_out": true if it didn't finish in time.
        """
        return self.request(
            {"type": "wait", "job_id": job_id, "timeout_s": timeout_s}, timeout_s=timeout_s + 10
        )

    def cancel(self, job_id: str) -> dict[str, object]:
        """Cancel a queued job.

        Args:
            job_id: the job.

        Returns:
            The cancelled JobView.
        """
        job = self.request({"type": "cancel", "job_id": job_id})["job"]
        return job if isinstance(job, dict) else {}

    def stats(self) -> dict[str, object]:
        """Get the StatsView.

        Returns:
            The stats dict.
        """
        stats = self.request({"type": "stats"})["stats"]
        return stats if isinstance(stats, dict) else {}
