"""The master's side of the forkserver: start it, ask it for workers, hear about their deaths.

WHY THIS MODULE EXISTS
    With start.method = forkserver, the master never forks workers itself. It starts one clean
    helper process (launch/forkserver_main.py) and sends it a `spawn_worker` request together
    with the worker's channel fd. The server forks and later reports `spawned` and `child_exit`.
    This module is the master's half of that conversation (spec/02 §4.3).

HOW IT FITS
    supervisor ──ForkserverLauncher.launch(spec)──▶ send_fds(control, [frame], [fd]) ──▶ Pending
    arbiter loop: control socket readable ──on_readable()──▶ [Spawned, ChildExit, ...]
    reap loop: waitpid returns the server's pid ──owns_pid() → True──▶ supervisor restarts it

READ FIRST
    docs/concepts/fd-passing.md, docs/concepts/reparenting-and-orphans.md,
    docs/concepts/start-methods.md

MILESTONE
    Introduced in K2.
"""

import os
import socket
import sys
import time

from kiln.config import Config
from kiln.errors import KilnError
from kiln.launch.base import ChildExit, Launched, LauncherEvent, Pending, Spawned, WorkerLaunchSpec
from kiln.launch.spawn import child_environment_for
from kiln.protocol.framing import FrameDecoder, encode_frame
from kiln.protocol.messages import MsgType


class ForkserverError(KilnError):
    """The forkserver didn't start (preload failed, or no `ready` in time).

    Why:
        The master can't serve without it; the arbiter turns this into exit code 4.
    """


class ForkserverLauncher:
    """Start workers through a forkserver process.

    Why:
        Workers come from the server's clean memory, not the master's, and the master stays
        free of the app (with app.preload, the *server* imports it).
    """

    name = "forkserver"
    reaps_children = False

    def __init__(self, config: Config) -> None:
        """Remember the config; start() launches the server.

        Args:
            config: the configuration.
        """
        self.config = config
        self.control: socket.socket | None = None
        self.pid = 0
        self.decoder = FrameDecoder(config.protocol.max_frame_bytes)
        self.preloaded: list[str] = []

    def start(self) -> None:
        """posix_spawn the server and wait (bounded) for its `ready`.

        Why:
            Waiting here is acceptable: it happens at startup or right after the server died,
            before workers can exist, and it's bounded by master.boot_timeout_s.

        Raises:
            ForkserverError: the server failed to preload or didn't answer in time.
        """
        master_end, server_end = socket.socketpair()
        os.set_inheritable(server_end.fileno(), True)
        argv = [
            sys.executable,
            "-m",
            "kiln.launch.forkserver_main",
            "--control-fd",
            str(server_end.fileno()),
            "--config-json",
            self.config.to_json(),
        ]
        # SYSCALL: posix_spawn: the server is a fresh interpreter (clean memory) and our child.
        self.pid = os.posix_spawn(sys.executable, argv, child_environment_for(self.config))
        server_end.close()
        master_end.settimeout(self.config.master.boot_timeout_s)
        self.decoder = FrameDecoder(self.config.protocol.max_frame_bytes)
        ready = self._read_one(master_end)
        if ready.get("type") != MsgType.READY:
            raise ForkserverError(f"forkserver failed to start: {ready}")
        self.preloaded = [str(name) for name in ready.get("preload", [])]  # type: ignore[attr-defined]
        master_end.setblocking(False)  # WHY: from now on it is watched by the master's selector
        self.control = master_end

    def _read_one(self, sock: socket.socket) -> dict[str, object]:
        """Read exactly one message, blocking up to the socket's timeout.

        Why:
            At startup the master has nothing else to do yet, so a bounded blocking read of the
            `ready` message is simpler than a selector, and still can't hang forever.

        Args:
            sock: the control socket.

        Returns:
            The message.

        Raises:
            ForkserverError: timeout or EOF.
        """
        deadline = time.monotonic() + self.config.master.boot_timeout_s
        while time.monotonic() < deadline:
            try:
                data = sock.recv(65536)
            except TimeoutError as error:
                raise ForkserverError("forkserver did not report ready in time") from error
            if not data:
                raise ForkserverError("forkserver exited during startup (see its log)")
            messages = self.decoder.feed(data)
            if messages:
                return messages[0]
        raise ForkserverError("forkserver did not report ready in time")

    def launch(self, spec: WorkerLaunchSpec) -> Launched | Pending:
        """Ask the server to fork a worker; the channel fd travels with the request.

        Why:
            send_fds puts the frame bytes and the fd in ONE sendmsg call, so the server receives
            the fd together with (or before) the frame that needs it.

        Args:
            spec: what to start.

        Returns:
            Pending: the pid arrives later in a Spawned event.

        Raises:
            ForkserverError: the server isn't running.
        """
        if self.control is None:
            raise ForkserverError("forkserver is not running")
        frame = encode_frame(
            {
                "type": str(MsgType.SPAWN_WORKER),
                "worker_id": spec.worker_id,
                "generation": spec.generation,
            }
        )
        # CONCEPT(fd-passing): SCM_RIGHTS: the kernel installs a copy of the fd in the server.
        # WHY: the control socket is non-blocking; a tiny frame always fits in the buffer.
        socket.send_fds(self.control, [frame], [spec.channel_child.fileno()])
        return Pending()

    def selectable(self) -> socket.socket | None:
        """The control socket, watched by the master's selector.

        Returns:
            The socket, or None while the server is down.
        """
        return self.control

    def on_readable(self) -> list[LauncherEvent]:
        """Turn the server's messages into Spawned and ChildExit events.

        Why:
            The supervisor shouldn't parse forkserver frames; it reacts to typed events, the
            same way whichever launcher produced them.

        Returns:
            The events (empty if the server closed the socket; its death is seen by reaping).
        """
        if self.control is None:
            return []
        events: list[LauncherEvent] = []
        while True:
            try:
                data = self.control.recv(65536)
            except BlockingIOError:
                break
            except OSError:
                data = b""
            if not data:
                self.control.close()
                self.control = None
                break
            for msg in self.decoder.feed(data):
                if msg.get("type") == MsgType.SPAWNED:
                    events.append(Spawned(int(str(msg["worker_id"])), int(str(msg["pid"]))))
                elif msg.get("type") == MsgType.CHILD_EXIT:
                    events.append(ChildExit(int(str(msg["pid"])), int(str(msg["wait_status"]))))
        return events

    def owns_pid(self, pid: int) -> bool:
        """Is this the forkserver process itself?

        Args:
            pid: a pid returned by the master's waitpid.

        Returns:
            True for the server.
        """
        return pid == self.pid

    def extra_fds(self) -> list[int]:
        """The control socket (no forked process should keep it).

        Returns:
            Its fd, if open.
        """
        return [self.control.fileno()] if self.control is not None else []

    def stop(self) -> None:
        """Tell the server to exit once its children are gone."""
        if self.control is not None:
            try:
                self.control.send(encode_frame({"type": str(MsgType.SHUTDOWN)}))
            except OSError:
                pass  # WHY: the server is already gone
