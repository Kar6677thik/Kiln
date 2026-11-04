"""The forkserver process: a small, clean helper whose only job is to fork workers on request.

WHY THIS MODULE EXISTS
    Forking the *master* copies all of the master's state into every worker: its sockets, its
    job table, whatever it has imported. A forkserver is started early from a fresh interpreter,
    imports only a chosen preload list (Kiln, and the app if app.preload), and then forks
    workers from *that* clean state when the master asks (docs/concepts/start-methods.md).
    Because the workers are the forkserver's children, only the forkserver can reap them, so
    it also reports every exit status back to the master (spec/02 §4.3, spec/04 §6).

HOW IT FITS
    master (launch/forkserver.py) ──posix_spawn──▶ python -m kiln.launch.forkserver_main
    master ──spawn_worker frame + 1 fd (send_fds)──▶ server ──os.fork──▶ worker (run_worker)
    server ──spawned{worker_id, pid}──▶ master
    server ──SIGCHLD → waitpid──▶ child_exit{pid, wait_status}──▶ master
    master dies (control socket EOF) ──▶ server SIGTERMs, then SIGKILLs, its children and exits

READ FIRST
    docs/concepts/fd-passing.md (SCM_RIGHTS and the fd FIFO),
    docs/concepts/reparenting-and-orphans.md,
    docs/concepts/zombies-and-reaping.md

MILESTONE
    Introduced in K2.
"""

import argparse
import contextlib
import importlib
import logging
import os
import selectors
import signal
import socket
import sys
import time

from kiln import logsetup
from kiln.app import App, AppLoadError, load_app
from kiln.config import Config
from kiln.labs import preload as preload_graph
from kiln.launch.base import WorkerLaunchSpec
from kiln.launch.fork import fork_child
from kiln.memory import gcmodes
from kiln.protocol.framing import FrameDecoder, encode_frame
from kiln.protocol.messages import MsgType

# GOTCHA: run as `python -m kiln.launch.forkserver_main`, this module's __name__ is "__main__",
# so getLogger(__name__) would create a logger OUTSIDE the configured "kiln" tree and every line
# would silently vanish. Name the logger explicitly.
log = logging.getLogger("kiln.launch.forkserver_main")
CHILD_KILL_GRACE_S = 5.0  # K2 spec §5.4: SIGTERM, wait up to 5 s, then SIGKILL


class ForkServer:
    """The server's state: its control socket, received fds, children, and the preloaded app.

    Why:
        One object owns everything the server loop touches; no globals (code-style §6).
    """

    def __init__(self, control: socket.socket, config: Config, app: App | None) -> None:
        """Set up the server around its control socket.

        Why:
            The SIGCHLD wakeup pair is created here, like the master's SignalPipe, so child
            deaths arrive as ordinary selector events.

        Args:
            control: the server's end of the socketpair with the master.
            config: the configuration.
            app: the preloaded app (passed to every forked worker), or None.
        """
        self.control = control
        self.config = config
        self.app = app
        self.decoder = FrameDecoder(config.protocol.max_frame_bytes)
        # CONCEPT(fd-passing): fds arrive in the ancillary data of recvmsg, separately from the
        # frame bytes. We queue them in order; each decoded spawn_worker frame takes one.
        self.fd_fifo: list[int] = []
        self.children: set[int] = set()
        self.accepting = True
        self.wake_read, self.wake_write = socket.socketpair()
        self.wake_read.setblocking(False)
        self.wake_write.setblocking(False)
        signal.set_wakeup_fd(self.wake_write.fileno(), warn_on_full_buffer=False)
        signal.signal(signal.SIGCHLD, self._on_signal)
        # WHY (ADR-024): Ctrl-C reaches the whole process group; only the master decides.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        self.selector = selectors.DefaultSelector()
        self.selector.register(control, selectors.EVENT_READ, "control")
        self.selector.register(self.wake_read, selectors.EVENT_READ, "wakeup")

    def _on_signal(self, signum: int, frame: object) -> None:
        """SIGCHLD handler: nothing to do here; the wakeup byte makes the loop reap.

        Args:
            signum: the signal number.
            frame: the interrupted frame (unused).
        """

    def send(self, msg: dict[str, object]) -> None:
        """Send one frame to the master (the control socket is blocking in the server).

        Args:
            msg: the message.
        """
        self.control.sendall(encode_frame(msg, self.config.protocol.max_frame_bytes))

    def run(self) -> int:
        """The server loop: until the master goes away or asks us to stop.

        Why:
            A single-threaded selector loop (like the master's) can wait for "a request arrived"
            and "a child died" at the same time without ever forking with threads alive.

        Returns:
            The exit code.
        """
        while True:
            for key, _ in self.selector.select(timeout=1.0):
                if key.data == "wakeup":
                    self._drain_wakeup()
                    self._reap()
                elif not self._on_control():
                    return self._master_gone()
            self._reap()  # WHY: a safety net, in case a SIGCHLD byte was dropped
            if not self.accepting and not self.children:
                return 0

    def _drain_wakeup(self) -> None:
        """Discard the wakeup bytes (the signal itself is handled by reaping)."""
        with contextlib.suppress(BlockingIOError):
            while self.wake_read.recv(4096):
                pass

    def _on_control(self) -> bool:
        """Read the control socket: frames plus any passed fds.

        Why:
            socket.recv_fds returns the bytes AND the fds sent in the same sendmsg; keeping the
            fds in arrival order pairs each with its spawn_worker frame (the fd FIFO design).

        Returns:
            False if the master closed the socket.
        """
        # SYSCALL: recv_fds → recvmsg(2) with SCM_RIGHTS ancillary data.
        data, fds, _flags, _addr = socket.recv_fds(self.control, 65536, 16)
        self.fd_fifo.extend(fds)
        if not data:
            return False
        for msg in self.decoder.feed(data):
            kind = msg.get("type")
            if kind == MsgType.SPAWN_WORKER:
                self._spawn(msg)
            elif kind == MsgType.SHUTDOWN:
                self.accepting = False
        return True

    def _spawn(self, msg: dict[str, object]) -> None:
        """Fork one worker with the fd that arrived with this request.

        Why:
            The forked child is a copy of THIS clean server, not of the master.

        Args:
            msg: the spawn_worker message.
        """
        if not self.fd_fifo:
            # WHY: a spawn_worker frame with no fd is a protocol error (K2 spec §5.4).
            logsetup.event(
                log, logging.CRITICAL, "forkserver_protocol_error", detail="spawn_worker without fd"
            )
            sys.exit(1)
        fd = self.fd_fifo.pop(0)
        worker_id = int(str(msg.get("worker_id")))
        generation = int(str(msg.get("generation")))
        channel = socket.socket(fileno=fd)
        own = [
            self.control.fileno(),
            self.wake_read.fileno(),
            self.wake_write.fileno(),
            self.selector.fileno(),
        ]
        spec = WorkerLaunchSpec(
            worker_id, generation, self.config, channel, tuple(own + self.fd_fifo), self.app
        )
        pid = fork_child(spec)
        # Parent (the server): its copy of the worker's fd is no longer needed.
        channel.close()
        self.children.add(pid)
        self.send({"type": str(MsgType.SPAWNED), "worker_id": worker_id, "pid": pid})
        logsetup.event(log, logging.DEBUG, "forkserver_forked", worker_id=worker_id, pid_worker=pid)

    def _reap(self) -> None:
        """Collect dead children and report each wait status to the master.

        Why:
            Only a parent can waitpid its children, and the workers are the server's children.
            Without this, dead workers would stay zombies and the master would never learn how
            they died (docs/concepts/zombies-and-reaping.md).
        """
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                return
            self.children.discard(pid)
            with contextlib.suppress(OSError):
                self.send({"type": str(MsgType.CHILD_EXIT), "pid": pid, "wait_status": status})
            logsetup.event(
                log, logging.DEBUG, "forkserver_child_exit", pid_worker=pid, wait_status=status
            )

    def _master_gone(self) -> int:
        """The master died: stop every child we started, so none run on as orphans.

        Why:
            K2 spec §5.4: without this, a crashed master would leave workers running forever
            (AC-2.3: no worker survives longer than 6 s).

        Returns:
            The exit code (0).
        """
        logsetup.event(log, logging.WARNING, "forkserver_master_gone", children=len(self.children))
        for pid in list(self.children):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + CHILD_KILL_GRACE_S
        while self.children and time.monotonic() < deadline:
            self._reap_quietly()
            time.sleep(0.05)  # WHY: shutting down; nothing else to serve
        for pid in list(self.children):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        return 0

    def _reap_quietly(self) -> None:
        """Reap without reporting (the master is gone).

        Why:
            During the master-gone shutdown there is nobody to report to, but children must
            still be reaped so we can tell when they have all exited.
        """
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                self.children.clear()
                return
            if pid == 0:
                return
            self.children.discard(pid)


def preload(config: Config) -> tuple[App | None, list[str]]:
    """Import the forkserver_preload modules and, if app.preload, the app.

    Why:
        Everything imported here is inherited by every worker the server forks, so it's loaded
        once and its memory pages are shared (K2 spec §5.4).

    Args:
        config: the configuration.

    Returns:
        (the app or None, the names that were imported).
    """
    names = []
    for name in config.start.forkserver_preload:
        importlib.import_module(name)
        names.append(name)
    app = None
    if config.app.preload and config.app.spec:
        app = load_app(config.app.spec, config.app.pythonpath)
        names.append(config.app.spec)
    return app, names


def main(argv: list[str]) -> int:
    """Start the server: parse args, preload, say `ready`, serve.

    Why:
        The `ready` message (with our pid and what was preloaded) is what unblocks the
        master's ForkserverLauncher.start(); a preload failure is reported as `boot_failed`
        so the master can exit with code 4 instead of waiting for a timeout.

    Args:
        argv: command-line arguments (--control-fd, --config-json).

    Returns:
        The exit code (0 normal, 4 if the app failed to preload).
    """
    parser = argparse.ArgumentParser(prog="python -m kiln.launch.forkserver_main")
    parser.add_argument("--control-fd", type=int, required=True)
    parser.add_argument("--config-json", required=True)
    args = parser.parse_args(argv)
    config = Config.from_json(args.config_json)
    logsetup.configure("forkserver", config.log.level, config.log.format, config.log.file)
    control = socket.socket(fileno=args.control_fd)
    started = time.monotonic()
    # WHY (K3 §5.5): the forkserver is the "parent" whose memory the workers share.
    if config.labs.gc_watch:
        gcmodes.install_watch(config.worker.gc_mode, in_parent=True, on_event=gcmodes.log_gc_event)
    gcmodes.apply_parent_early(config.worker.gc_mode)
    try:
        app, names = preload(config)
        graph = preload_graph.ensure_graph(config.labs.preload_mb, config.labs.preload_composition)
    except AppLoadError as error:
        logsetup.event(log, logging.CRITICAL, "forkserver_preload_failed", error=str(error))
        control.sendall(
            encode_frame(
                {
                    "type": "boot_failed",
                    "error": {"type": "AppLoadError", "message": str(error), "traceback": ""},
                }
            )
        )
        return 4
    server = ForkServer(control, config, app)
    elapsed = round((time.monotonic() - started) * 1000, 2)
    server.send(
        {"type": str(MsgType.READY), "pid": os.getpid(), "preload": names, "preload_ms": elapsed}
    )
    logsetup.event(
        log, logging.INFO, "forkserver_ready", preload=",".join(names), preload_ms=elapsed
    )
    if graph is not None:
        logsetup.event(
            log,
            logging.INFO,
            "preload_graph_built",
            records=len(graph.records),
            traced_mib=round(graph.traced_bytes / 2**20, 1),
            build_s=graph.build_s,
        )
    return server.run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
