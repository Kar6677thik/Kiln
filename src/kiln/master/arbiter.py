"""The arbiter: the master's single-threaded event loop, wiring every master component together.

WHY THIS MODULE EXISTS
    The master must react to many things at once: new clients, client requests, worker
    messages, worker deaths, signals and deadlines. With one thread (ADR-006) the only way is
    an event loop: wait until *something* is ready, handle it quickly, repeat. This module is
    that loop, exactly as docs/spec/01-architecture.md §4 describes, plus the code that answers
    each client request (docs/spec/02-protocol.md §3).

HOW IT FITS
    cli `serve` ──Arbiter(config).run()──▶ exit code
    loop: selector.select(timeout = next timer) ──▶ listener / signal pipe / clients / workers
          ──▶ JobTable (jobs.py), Supervisor (supervisor.py), dispatch (dispatcher.py)
          ──▶ flush output buffers ──▶ repeat until shutdown completes

READ FIRST
    docs/concepts/event-loops.md, docs/concepts/non-blocking-io-and-selectors.md,
    docs/spec/01-architecture.md §4, docs/python/selectors-module.md

MILESTONE
    Introduced in K1.
"""

import logging
import os
import platform
import selectors
import signal
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from kiln import __version__, logsetup
from kiln.app import App, AppLoadError, load_app
from kiln.config import Config, ConfigError, load_config
from kiln.errors import ErrorCode
from kiln.launch.forkserver import ForkserverError
from kiln.master import dispatcher
from kiln.master.connections import ACCEPT_BATCH, MAX_CLIENTS, Connection, PeerClosed
from kiln.master.jobs import TERMINAL, Job, JobTable, NotCancellable, NotFound, QueueFull, view
from kiln.master.signals import SignalPipe
from kiln.master.supervisor import Supervisor
from kiln.master.timers import TimerHeap
from kiln.master.workers import WorkerHandle, WorkerTable
from kiln.protocol.framing import FrameError, FrameTooLarge
from kiln.protocol.messages import (
    BadRequest,
    BootFailed,
    Cancel,
    ClientRequest,
    Debug,
    Heartbeat,
    Ping,
    Ready,
    Result,
    Retiring,
    Stats,
    Status,
    Submit,
    Wait,
    error,
    ok,
    parse_client_request,
    parse_worker_message,
    request_id_of,
)

log = logging.getLogger(__name__)

TICK_S = 1.0  # spec/01 §4: wake at least once a second (heartbeat checks, reaping, eviction)
RSS_CHECK_EVERY = 5  # ticks between master-side RSS checks (spec/04 §5.5)
EXIT_CODES = {"graceful": 0, "fast": 1, "immediate": 1, "give_up": 4}


@dataclass(slots=True)
class Waiter:
    """A client blocked on `wait` for one job.

    Why:
        The reply is sent later, when the job finishes or the wait times out.
    """

    conn: Connection
    req_id: int
    timer_id: int


class Arbiter:
    """The master process: loop, listener, clients, and the components it coordinates.

    Why:
        One object holds the whole master state, so every component can reach what it needs
        through it (supervisor.arb.jobs, ...), and nothing is global.
    """

    def __init__(self, config: Config, config_path: Path | None = None) -> None:
        """Prepare (but don't start) the master.

        Why:
            Builds every master component but starts nothing, so tests and run() control when
            sockets and signals appear.

        Args:
            config: the validated configuration.
            config_path: the config file, re-read on SIGHUP (None: no file).
        """
        self.config = config
        self.config_path = config_path
        # PY: DefaultSelector picks the best mechanism for this OS (epoll on Linux).
        self.selector = selectors.DefaultSelector()
        self.timers = TimerHeap()
        self.jobs = JobTable(
            config.queue.max_size, config.jobs.max_retained, config.jobs.result_ttl_s
        )
        self.jobs.on_terminal = self._on_job_terminal
        self.workers = WorkerTable()
        self.clients: dict[int, Connection] = {}
        self.waiters: dict[str, list[Waiter]] = {}
        self.job_timers: dict[tuple[str, int], int] = {}
        self.manifest: dict[str, object] = {}
        self.manifest_generation = -1
        self.preloaded_app: App | None = None
        self.listener: socket.socket | None = None
        self.signals: SignalPipe | None = None
        self.supervisor = Supervisor(self)
        self.shutting_down = False
        self.shutdown_mode = ""
        self.exit_code = 0
        self.started_mono = time.monotonic()
        self._finished = False
        self._dirty: set[Connection] = set()
        self._masks: dict[int, int] = {}
        self._dispatch_needed = False
        self._ticks = 0

    # --- helpers used by the other components ------------------------------------------------

    def send(self, conn: Connection, msg: dict[str, object]) -> None:
        """Queue a message on a connection; it is flushed at the end of this loop pass.

        Args:
            conn: the connection.
            msg: the message.
        """
        if conn.closed:
            return
        conn.send(msg)
        self._dirty.add(conn)

    def register_worker(self, handle: WorkerHandle) -> None:
        """Start watching a new worker's channel.

        Args:
            handle: the new worker.
        """
        self.workers.add(handle)
        self._register(handle.conn, ("worker", handle))

    def unregister_worker(self, handle: WorkerHandle) -> None:
        """Forget a reaped worker and close its channel.

        Args:
            handle: the dead worker.
        """
        self._close_conn(handle.conn)
        self.workers.remove(handle)

    def child_close_fds(self) -> list[int]:
        """Every fd a forked worker must close (spec/04 §6 rule 1).

        Why:
            A forked worker inherits every open fd; leaving the master's sockets open in workers
            causes subtle bugs (spec/04 §6 rule 1).

        Returns:
            Listener, client sockets, other workers' channels, the signal pipe, the selector.
        """
        fds = [conn.fileno() for conn in self.clients.values()]
        fds += [h.conn.fileno() for h in self.workers.all() if not h.conn.closed]
        if self.listener is not None:
            fds.append(self.listener.fileno())
        if self.signals is not None:
            fds += self.signals.fds()
        # WHY: the epoll fd itself is inherited too; the worker has no use for it.
        fds.append(self.selector.fileno())
        fds += [h.pidfd for h in self.workers.all() if h.pidfd is not None]
        return fds

    def set_manifest(self, manifest: dict[str, object], generation: int) -> None:
        """Adopt a worker's manifest (the first READY worker of each generation, ADR-013).

        Why:
            The master validates submits against the workers' manifest (ADR-013); the first READY
            worker of a generation sets it.

        Args:
            manifest: {"tasks": {...}} from the ready message.
            generation: that worker's generation.
        """
        if generation <= self.manifest_generation:
            return
        old = set(self.manifest_tasks())
        self.manifest = manifest
        self.manifest_generation = generation
        new = set(self.manifest_tasks())
        if generation > 0:
            logsetup.event(
                log,
                logging.INFO,
                "manifest_switched",
                gen=generation,
                tasks_added=len(new - old),
                tasks_removed=len(old - new),
            )
        if self.listener is None and not self.shutting_down:
            self._open_listener()

    def manifest_tasks(self) -> dict[str, dict[str, object]]:
        """The current manifest's tasks.

        Returns:
            {task name: options}.
        """
        tasks = self.manifest.get("tasks", {})
        return tasks if isinstance(tasks, dict) else {}

    def dispatch_soon(self) -> None:
        """Ask the loop to run the dispatcher at the end of this pass."""
        self._dispatch_needed = True

    # --- startup -----------------------------------------------------------------------------

    def _register(self, conn: Connection, data: tuple[str, object]) -> None:
        """Watch a connection for readability.

        Args:
            conn: the connection.
            data: (kind, object) handed back by the selector when it's ready.
        """
        self.selector.register(conn.sock, selectors.EVENT_READ, data)
        self._masks[conn.fileno()] = selectors.EVENT_READ

    def _open_listener(self) -> None:
        """Open the KP/1 listening socket (only after the first READY worker, ADR-013).

        Why:
            Clients are only accepted once a worker can run their jobs (ADR-013); a non-blocking
            listener keeps the loop responsive.
        """
        host, _, port = self.config.master.bind.rpartition(":")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # WHY: SO_REUSEADDR lets a restarted master bind the port right away, even while old
        # connections are still in TCP's TIME_WAIT state.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, int(port)))
        sock.listen(512)
        sock.setblocking(False)  # WHY: accept() must never block the loop (ADR-006)
        self.listener = sock
        self.selector.register(sock, selectors.EVENT_READ, ("listener", None))
        actual_host, actual_port = sock.getsockname()
        logsetup.event(
            log, logging.INFO, "listening", bind=f"{actual_host}:{actual_port}", fd=sock.fileno()
        )

    def _write_pidfile(self) -> None:
        """Write our pid to master.pidfile, if configured (used by kiln reload/stop/scale)."""
        if self.config.master.pidfile:
            Path(self.config.master.pidfile).write_text(f"{os.getpid()}\n", encoding="ascii")

    def _remove_pidfile(self) -> None:
        """Remove the pidfile at exit, but only if it still names us.

        Why:
            A pidfile that names someone else's process must never be deleted, so we check it still
            names us.
        """
        path = Path(self.config.master.pidfile) if self.config.master.pidfile else None
        if (
            path is not None
            and path.exists()
            and path.read_text(encoding="ascii").strip() == str(os.getpid())
        ):
            path.unlink()

    def _start(self) -> bool:
        """Startup in the order of K1 spec §5.6: logging, pidfile, signals, preload, workers.

        Why:
            The startup order matters: signals must be caught before the first fork, and the app
            preloaded before workers copy it.

        Returns:
            False if the app could not be preloaded.
        """
        cfg = self.config
        logsetup.configure("master", cfg.log.level, cfg.log.format, cfg.log.file)
        logsetup.event(
            log,
            logging.INFO,
            "master_starting",
            version=__version__,
            python=platform.python_version(),
            start_method=cfg.start.method,
            executor=cfg.worker.executor,
            workers=cfg.master.workers,
            app_spec=cfg.app.spec,
            preload=cfg.app.preload,
        )
        self._write_pidfile()
        self.signals = SignalPipe()
        self.selector.register(self.signals.read_sock, selectors.EVENT_READ, ("signals", None))
        events = self.supervisor.oom_events
        logsetup.event(
            log,
            logging.INFO,
            "cgroup_detected",
            path=str(events.parent) if events else None,
            memory_events="readable" if events else "unavailable",
        )
        method = self.supervisor.launcher.name
        if cfg.app.preload and method == "spawn":
            # WHY (K2 spec §5.3): a spawned worker is a fresh interpreter and imports the app
            # itself; nothing preloaded here could reach it.
            logsetup.event(
                log,
                logging.WARNING,
                "preload_ignored",
                reason="app.preload has no effect with start.method=spawn: "
                "each worker imports the app itself",
            )
        if cfg.app.preload and method == "fork":
            started = time.monotonic()
            try:
                self.preloaded_app = load_app(cfg.app.spec, cfg.app.pythonpath)
            except AppLoadError as problem:
                logsetup.event(
                    log,
                    logging.CRITICAL,
                    "boot_failure_limit_reached",
                    consecutive_failures=0,
                    last_error=str(problem),
                )
                return False
            logsetup.event(
                log,
                logging.INFO,
                "app_preloaded",
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                tasks=len(self.preloaded_app.tasks()),
            )
        try:
            # WHY: forkserver: start the server now (it preloads the app, not the master).
            self.supervisor.start_launcher()
        except ForkserverError as problem:
            logsetup.event(log, logging.CRITICAL, "forkserver_start_failed", error=str(problem))
            return False
        self.watch_launcher_socket()
        self.timers.schedule(time.monotonic() + TICK_S, "tick")
        self.supervisor.ensure_workers()
        return True

    # --- the loop ----------------------------------------------------------------------------

    def run(self) -> int:
        """Run the master until shutdown completes; return the process exit code.

        Why:
            The master's whole life is this loop; returning the exit code (not calling exit) keeps
            it testable.

        Returns:
            0 clean, 1 forced, 4 app failed to boot (spec/04 §5.6).
        """
        if not self._start():
            self._cleanup()
            return 4
        while not self._finished:
            self._loop_once()
        self._cleanup()
        return self.exit_code

    def _loop_once(self) -> None:
        """One pass of the loop of spec/01 §4.

        Why:
            One pass of spec/01 §4: wait for events, handle them, run timers, dispatch, flush.
            Repeating it is the master.
        """
        now = time.monotonic()
        deadline = self.timers.next_deadline()
        timeout = TICK_S if deadline is None else max(0.0, min(TICK_S, deadline - now))
        # SYSCALL: selector.select → epoll_wait(2): sleep until a socket is ready or timeout.
        for key, mask in self.selector.select(timeout):
            kind, obj = key.data
            if kind == "listener":
                self._accept_clients()
            elif kind == "signals":
                self._handle_signals()
            elif kind == "client":
                self._on_client_event(obj, mask)
            elif kind == "worker":
                self._on_worker_event(obj, mask)
            elif kind == "launcher":
                self._on_launcher_readable()
            elif kind == "orphan":
                self.supervisor.on_orphan_exit(obj)
        for timer in self.timers.pop_expired(time.monotonic()):
            self._on_timer(timer.kind, timer.key)
        if self._dispatch_needed or self.jobs.queue:
            self._dispatch_needed = False
            dispatcher.dispatch(self)
        self._flush_dirty()
        self._check_shutdown_done()

    def _flush_dirty(self) -> None:
        """Write pending output and watch for writability only where output is left over.

        Why:
            spec/01 §4 rule 3: a non-blocking send may take only part of the data; the rest
            waits until the socket is writable again.
        """
        for conn in list(self._dirty):
            self._dirty.discard(conn)
            if conn.closed:
                continue
            try:
                done = conn.flush()
            except PeerClosed:
                self._drop_conn(conn)
                continue
            if done and conn.close_after_flush:
                self._drop_conn(conn)
                continue
            if conn.overflowing:
                logsetup.event(
                    log,
                    logging.WARNING,
                    "client_dropped",
                    reason="slow_reader",
                    outbuf=len(conn.outbuf),
                )
                self._drop_conn(conn)
                continue
            want = selectors.EVENT_READ | (0 if done else selectors.EVENT_WRITE)
            if self._masks.get(conn.fileno()) != want:
                self.selector.modify(conn.sock, want, self.selector.get_key(conn.sock).data)
                self._masks[conn.fileno()] = want

    def _accept_clients(self) -> None:
        """Accept up to ACCEPT_BATCH new client connections.

        Why:
            Accepting in batches stops a flood of connections from starving workers and timers
            (ADR-031).
        """
        if self.listener is None:
            return
        for _ in range(ACCEPT_BATCH):
            try:
                sock, peer = self.listener.accept()
            except BlockingIOError:
                return  # SYSCALL: accept(2) returned EAGAIN: nobody else is waiting
            if len(self.clients) >= MAX_CLIENTS:
                logsetup.event(
                    log, logging.WARNING, "client_rejected", reason="max_clients", peer=str(peer)
                )
                sock.close()
                continue
            conn = Connection(sock, "client", self.config.protocol.max_frame_bytes)
            self.clients[conn.fileno()] = conn
            self._register(conn, ("client", conn))
            logsetup.event(
                log, logging.DEBUG, "client_connected", peer=str(peer), conn_id=conn.fileno()
            )

    def _close_conn(self, conn: Connection) -> None:
        """Stop watching and close a connection (idempotent).

        Why:
            Unregistering from the selector before closing avoids watching an fd number the OS may
            soon reuse.

        Args:
            conn: the connection.
        """
        if conn.closed:
            return
        try:
            self.selector.unregister(conn.sock)
        except (KeyError, ValueError):
            pass  # WHY: already unregistered
        self._masks.pop(conn.fileno(), None)
        conn.close()

    def _drop_conn(self, conn: Connection) -> None:
        """Close a client connection and forget its waiters.

        Why:
            A gone client can't receive replies; its parked waits must be forgotten, or their timers
            would write to a closed socket.

        Args:
            conn: a client connection.
        """
        self.clients.pop(conn.fileno(), None)
        for job_id in list(self.waiters):
            remaining = [w for w in self.waiters[job_id] if w.conn is not conn]
            for gone in self.waiters[job_id]:
                if gone.conn is conn:
                    self.timers.cancel(gone.timer_id)
            if remaining:
                self.waiters[job_id] = remaining
            else:
                del self.waiters[job_id]
        self._close_conn(conn)

    # --- signals and timers ------------------------------------------------------------------

    def _handle_signals(self) -> None:
        """Act on every signal recorded since the last pass (spec/04 §3).

        Why:
            Signal handlers only record signals (docs/concepts/signals.md); here, at a safe point in
            the loop, we act on them.
        """
        if self.signals is None:
            return
        for sig in self.signals.drain():
            action = self._signal_action(sig)
            level = logging.DEBUG if sig is signal.SIGCHLD else logging.INFO
            logsetup.event(log, level, "signal_received", signal=sig.name, action=action)
            if action == "reap":
                self.supervisor.reap()
            elif action == "reload":
                self._reload()
            elif action in ("graceful_shutdown", "fast_shutdown", "immediate_shutdown"):
                self.begin_shutdown(action.removesuffix("_shutdown"))
            elif action == "scale_up":
                self.supervisor.scale(+1)
            elif action == "scale_down":
                self.supervisor.scale(-1)
            elif action == "status_dump":
                logsetup.event(
                    log,
                    logging.INFO,
                    "status_dump",
                    queue_depth=len(self.jobs.queue),
                    inflight=self.workers.inflight_total(),
                    workers=" ".join(
                        f"{h.worker_id}:{h.state}:{len(h.inflight)}" for h in self.workers.all()
                    ),
                )

    def _signal_action(self, sig: signal.Signals) -> str:
        """Map a signal to its action; a second SIGINT during shutdown means "fast" (spec/04 §3).

        Why:
            SIGINT means different things depending on state (graceful, then fast), so the mapping
            can't be a plain table.

        Args:
            sig: the signal.

        Returns:
            The action name.
        """
        if sig is signal.SIGINT:
            return "fast_shutdown" if self.shutting_down else "graceful_shutdown"
        table = {
            signal.SIGTERM: "graceful_shutdown",
            signal.SIGQUIT: "immediate_shutdown",
            signal.SIGHUP: "reload",
            signal.SIGTTIN: "scale_up",
            signal.SIGTTOU: "scale_down",
            signal.SIGUSR1: "status_dump",
            signal.SIGCHLD: "reap",
        }
        return table.get(sig, "ignored")

    def _reload(self) -> None:
        """SIGHUP: re-read the config file (if any), then start a new worker generation.

        Why:
            A reload with a broken config file must keep the current workers running, not leave the
            server without workers.
        """
        if self.config_path is not None:
            try:
                self.config = load_config(self.config_path, os.environ)
            except ConfigError as problem:
                logsetup.event(
                    log,
                    logging.ERROR,
                    "reload_aborted",
                    reason="config_invalid",
                    errors=str(problem),
                )
                return
        self.supervisor.reload()

    def _on_timer(self, kind: str, key: object) -> None:
        """Route an expired timer to its handler.

        Why:
            One switch that routes every kind of deadline to its handler keeps the heap generic
            (docs/concepts/event-loops.md).

        Args:
            kind: the timer kind.
            key: its key.
        """
        if kind == "tick":
            self._tick()
        elif kind == "job_timeout" and isinstance(key, tuple):
            self.supervisor.on_job_timeout(key[0], key[1])
            self.dispatch_soon()
        elif kind in ("boot_deadline", "retire_deadline") and isinstance(key, WorkerHandle):
            self.supervisor.on_deadline(kind, key)
        elif kind == "respawn":
            self.supervisor.on_respawn_timer()
        elif kind == "wait_timeout" and isinstance(key, tuple):
            self._on_wait_timeout(key[0], key[1])
        elif kind == "shutdown_deadline":
            self.supervisor.force_kill_remaining()

    def _tick(self) -> None:
        """Once a second: reap (safety net), heartbeats, RSS, eviction (spec/04 §4, §5.4, §5.5).

        Why:
            Some checks need no event to trigger them: hung workers send nothing, so we look every
            second instead.
        """
        self._ticks += 1
        now = time.monotonic()
        self.supervisor.reap()
        self.supervisor.check_heartbeats()
        if self._ticks % RSS_CHECK_EVERY == 0:
            self.supervisor.check_rss()
        evicted = self.jobs.evict_expired(now)
        if evicted:
            logsetup.event(log, logging.DEBUG, "jobs_evicted", count=evicted, reason="ttl")
        self.timers.schedule(now + TICK_S, "tick")

    # --- shutdown ----------------------------------------------------------------------------

    def begin_shutdown(self, mode: str) -> None:
        """Start a shutdown: graceful, fast, immediate or give_up (spec/04 §5.6).

        Why:
            Every way of stopping (Ctrl-C, SIGTERM, SIGQUIT, giving up) goes through here, so the
            steps of spec/04 §5.6 are written once.

        Args:
            mode: the shutdown mode.
        """
        if self.shutting_down and mode == "graceful":
            return
        upgrading = self.shutting_down
        self.shutting_down = True
        self.shutdown_mode = mode
        self.exit_code = EXIT_CODES.get(mode, 1)
        cancelled = []
        if not upgrading:
            if self.listener is not None:
                self.selector.unregister(self.listener)
                self.listener.close()
                self.listener = None
            cancelled = self.jobs.cancel_all_queued(
                "master shutting down", time.monotonic(), int(time.time() * 1000)
            )
        logsetup.event(
            log,
            logging.INFO,
            "shutdown_started",
            mode=mode,
            queued_cancelled=len(cancelled),
            inflight=self.workers.inflight_total(),
        )
        self.supervisor.stop_all(mode)
        grace = self.config.master.graceful_timeout_s if mode in ("graceful", "give_up") else 2.0
        self.timers.schedule(time.monotonic() + grace, "shutdown_deadline")

    def _check_shutdown_done(self) -> None:
        """Finish once shutting down and every worker has been reaped."""
        if not self.shutting_down or self.workers.all():
            return
        if self.supervisor.forced_kills and self.shutdown_mode == "graceful":
            self.exit_code = 1
        self._finished = True

    def _cleanup(self) -> None:
        """Stop the launcher, flush client replies (≤ 1 s), restore signals, remove the pidfile.

        Why:
            Clients waiting on replies deserve them, and the forkserver, the pidfile and the
            signal handlers must not outlive the master.
        """
        self._stop_launcher()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and any(c.wants_write for c in self.clients.values()):
            for conn in list(self.clients.values()):
                try:
                    conn.flush()
                except PeerClosed:
                    self._drop_conn(conn)
            time.sleep(0.01)  # WHY: shutdown only; the loop is no longer serving anyone
        for conn in list(self.clients.values()):
            self._drop_conn(conn)
        if self.listener is not None:
            self.listener.close()
        if self.signals is not None:
            self.signals.restore()
        self._remove_pidfile()
        logsetup.event(
            log,
            logging.INFO,
            "shutdown_finished",
            mode=self.shutdown_mode or "none",
            duration_ms=round((time.monotonic() - self.started_mono) * 1000, 1),
            forced_kills=self.supervisor.forced_kills,
            exit_code=self.exit_code,
        )

    # --- client requests ---------------------------------------------------------------------

    def _on_client_event(self, conn: Connection, mask: int) -> None:
        """A client socket is readable or writable.

        Why:
            Turns raw socket readiness into whole messages and handles broken frames by closing only
            that one client.

        Args:
            conn: the client connection.
            mask: which events fired.
        """
        if mask & selectors.EVENT_WRITE:
            self._dirty.add(conn)
        if not mask & selectors.EVENT_READ or conn.close_after_flush:
            return
        try:
            messages = conn.read()
        except PeerClosed:
            self._drop_conn(conn)
            return
        except FrameError as problem:
            code = (
                ErrorCode.PAYLOAD_TOO_LARGE
                if isinstance(problem, FrameTooLarge)
                else ErrorCode.BAD_FRAME
            )
            self.send(conn, error(None, code, str(problem)))
            conn.close_after_flush = True  # WHY: spec/02 §2.1: we can't resync a broken stream
            return
        for msg in messages:
            self._handle_request(conn, msg)

    def _handle_request(self, conn: Connection, msg: dict[str, object]) -> None:
        """Parse one client request and answer it (spec/02 §3).

        Why:
            Untrusted input enters here: parse errors become BAD_REQUEST, and bugs become INTERNAL
            instead of crashing the master.

        Args:
            conn: the client.
            msg: the decoded frame.
        """
        try:
            request = parse_client_request(msg)
        except BadRequest as problem:
            self.send(conn, error(request_id_of(msg), ErrorCode.BAD_REQUEST, str(problem)))
            return
        try:
            self._answer(conn, request)
        except (
            Exception
        ) as problem:  # WHY: spec/06 §2.5 internal_error: never let one request kill the master
            log.exception("internal_error", extra={"kv": {"where": type(request).__name__}})
            self.send(
                conn,
                error(request.req_id, ErrorCode.INTERNAL, f"{type(problem).__name__}: {problem}"),
            )

    def _answer(self, conn: Connection, request: ClientRequest) -> None:
        """Do what one valid request asks.

        Why:
            One branch per request type of spec/02 §3.1, each a few lines; the longer ones have
            their own functions.

        Args:
            conn: the client.
            request: the typed request.
        """
        if isinstance(request, Ping):
            self.send(conn, ok(request.req_id, pong=True, server_time_ms=int(time.time() * 1000)))
        elif isinstance(request, Submit):
            self._submit(conn, request)
        elif isinstance(request, Status):
            self._with_job(conn, request.req_id, request.job_id)
        elif isinstance(request, Wait):
            self._wait(conn, request)
        elif isinstance(request, Cancel):
            try:
                job = self.jobs.cancel(request.job_id, time.monotonic(), int(time.time() * 1000))
            except (NotFound, NotCancellable) as problem:
                self.send(
                    conn, error(request.req_id, problem.code or ErrorCode.INTERNAL, problem.message)
                )
                return
            logsetup.event(log, logging.INFO, "job_cancelled", job_id=job.job_id, reason="client")
            self.send(conn, ok(request.req_id, job=view(job)))
        elif isinstance(request, Stats):
            self.send(conn, ok(request.req_id, stats=self.stats()))
        elif isinstance(request, Debug):
            if not self.config.debug.enabled:
                self.send(
                    conn,
                    error(request.req_id, ErrorCode.DEBUG_DISABLED, "set debug.enabled = true"),
                )
            else:
                self.send(
                    conn, error(request.req_id, ErrorCode.BAD_REQUEST, "debug ops arrive in K6")
                )

    def _submit(self, conn: Connection, request: Submit) -> None:
        """Validate and queue a job (spec/02 §3.1).

        Why:
            Submit is where backpressure (QUEUE_FULL) and validation (UNKNOWN_TASK) happen, before a
            job ever exists.

        Args:
            conn: the client.
            request: the submit request.
        """
        tasks = self.manifest_tasks()
        if self.shutting_down:
            self.send(
                conn, error(request.req_id, ErrorCode.SHUTTING_DOWN, "master is shutting down")
            )
            return
        if not tasks:
            self.send(conn, error(request.req_id, ErrorCode.NOT_READY, "no worker is ready yet"))
            return
        options = tasks.get(request.task)
        if not isinstance(options, dict):
            logsetup.event(
                log,
                logging.INFO,
                "job_rejected",
                task=request.task,
                code="UNKNOWN_TASK",
                queue_depth=len(self.jobs.queue),
            )
            self.send(
                conn,
                error(request.req_id, ErrorCode.UNKNOWN_TASK, f"no task named {request.task!r}"),
            )
            return
        # WHY: the manifest came from a worker, as JSON, so check each value's type.
        default_timeout = options.get("timeout_s")
        default_attempts = options.get("max_attempts")
        listed = options.get("retry_on")
        timeout_s = request.timeout_s or (
            float(default_timeout) if isinstance(default_timeout, int | float) else 30.0
        )
        attempts = request.max_attempts or (
            default_attempts if isinstance(default_attempts, int) else 1
        )
        retry_on = frozenset(str(k) for k in listed) if isinstance(listed, list) else frozenset()
        try:
            job = self.jobs.submit(
                request.task,
                request.args,
                request.kwargs,
                (timeout_s, attempts, retry_on),
                time.monotonic(),
                int(time.time() * 1000),
            )
        except QueueFull as problem:
            logsetup.event(
                log,
                logging.INFO,
                "job_rejected",
                task=request.task,
                code="QUEUE_FULL",
                queue_depth=len(self.jobs.queue),
            )
            self.send(
                conn,
                error(
                    request.req_id,
                    ErrorCode.QUEUE_FULL,
                    problem.message,
                    retry_after_s=self.config.queue.retry_after_s,
                ),
            )
            return
        logsetup.event(
            log,
            logging.DEBUG,
            "job_submitted",
            job_id=job.job_id,
            task=job.task,
            queue_depth=len(self.jobs.queue),
        )
        self.send(conn, ok(request.req_id, job_id=job.job_id, state=str(job.state)))
        self.dispatch_soon()

    def _with_job(self, conn: Connection, req_id: int, job_id: str) -> None:
        """Reply with a job's JobView, or NOT_FOUND.

        Why:
            status and other lookups share the NOT_FOUND handling.

        Args:
            conn: the client.
            req_id: the request id.
            job_id: the job.
        """
        try:
            job = self.jobs.get(job_id)
        except NotFound as problem:
            self.send(conn, error(req_id, ErrorCode.NOT_FOUND, problem.message))
            return
        self.send(conn, ok(req_id, job=view(job)))

    def _wait(self, conn: Connection, request: Wait) -> None:
        """Reply now if the job is finished; otherwise park the request until it is.

        Why:
            A long-poll: instead of clients asking repeatedly, the master answers when the job
            finishes or the wait times out.

        Args:
            conn: the client.
            request: the wait request.
        """
        job = self.jobs.jobs.get(request.job_id)
        if job is None:
            self.send(
                conn, error(request.req_id, ErrorCode.NOT_FOUND, f"no job {request.job_id!r}")
            )
            return
        if job.state in TERMINAL:
            self.send(conn, ok(request.req_id, job=view(job)))
            return
        timer_id = self.timers.schedule(
            time.monotonic() + request.timeout_s, "wait_timeout", (request.job_id, request.req_id)
        )
        self.waiters.setdefault(request.job_id, []).append(Waiter(conn, request.req_id, timer_id))

    def _on_wait_timeout(self, job_id: str, req_id: int) -> None:
        """A `wait` timed out before its job finished: answer with timed_out=true.

        Why:
            A wait must answer by its timeout even if the job is still running, so clients are never
            stuck forever.

        Args:
            job_id: the job.
            req_id: the waiting request's id.
        """
        waiters = self.waiters.get(job_id, [])
        for waiter in waiters:
            if waiter.req_id == req_id:
                waiters.remove(waiter)
                job = self.jobs.jobs.get(job_id)
                body = view(job) if job is not None else None
                self.send(waiter.conn, ok(req_id, job=body, timed_out=True))
                break
        if not waiters:
            self.waiters.pop(job_id, None)

    def _on_job_terminal(self, job: Job) -> None:
        """JobTable callback: a job finished; wake every client waiting on it.

        Why:
            JobTable calls this for every finished job, so parked waits are answered the moment the
            result exists.

        Args:
            job: the finished job.
        """
        if str(job.state) == "FAILED":
            error_info = job.error or {}
            logsetup.event(
                log,
                logging.WARNING,
                "job_failed",
                job_id=job.job_id,
                task=job.task,
                attempt=len(job.attempts),
                error_kind=error_info.get("kind"),
                error_type=error_info.get("type"),
                error_message=error_info.get("message"),
            )
        for waiter in self.waiters.pop(job.job_id, []):
            self.timers.cancel(waiter.timer_id)
            self.send(waiter.conn, ok(waiter.req_id, job=view(job)))

    # --- worker messages ---------------------------------------------------------------------

    def _on_worker_event(self, handle: WorkerHandle, mask: int) -> None:
        """A worker channel is readable or writable.

        Why:
            A worker channel closing means the worker died; the death itself is handled when we reap
            it (spec/04 §4).

        Args:
            handle: the worker.
            mask: which events fired.
        """
        if mask & selectors.EVENT_WRITE:
            self._dirty.add(handle.conn)
        if not mask & selectors.EVENT_READ:
            return
        try:
            messages = handle.conn.read()
        except PeerClosed:
            # WHY: the worker exited (or crashed); its death is reported when we reap it.
            self._close_conn(handle.conn)
            self.workers.forget_fd(handle)
            return
        except FrameError as problem:
            logsetup.event(
                log,
                logging.CRITICAL,
                "protocol_violation",
                worker_id=handle.worker_id,
                detail=str(problem),
            )
            self.supervisor.kill(handle, signal.SIGKILL, "protocol_violation")
            return
        for msg in messages:
            self._handle_worker_message(handle, msg)

    # --- launcher (K2) ------------------------------------------------------------------------

    def watch_launcher_socket(self) -> None:
        """Register the launcher's socket (the forkserver control socket) with the selector."""
        sock = self.supervisor.launcher.selectable()
        if sock is not None:
            self.selector.register(sock, selectors.EVENT_READ, ("launcher", None))

    def forget_launcher_socket(self) -> None:
        """Unregister the launcher's socket (the forkserver closed it or died)."""
        for key in list(self.selector.get_map().values()):
            if key.data == ("launcher", None):
                self.selector.unregister(key.fileobj)

    def _on_launcher_readable(self) -> None:
        """Read forkserver messages and hand the events to the supervisor.

        Why:
            The arbiter owns the selector; the supervisor owns worker state. This method is
            the bridge, and it drops the socket from the selector once the server closed it.
        """
        launcher = self.supervisor.launcher
        events = launcher.on_readable()
        if launcher.selectable() is None:
            self.forget_launcher_socket()
        self.supervisor.on_launcher_events(events)
        self.dispatch_soon()

    def watch_orphan(self, handle: WorkerHandle) -> None:
        """Watch an orphaned worker's pidfd: it becomes readable when the process exits.

        Why:
            spec/04 §6 rule 4: a pidfd is the only pid-reuse-safe way to notice a non-child's exit.

        Args:
            handle: the orphaned worker (handle.pidfd is set).
        """
        if handle.pidfd is not None:
            self.selector.register(handle.pidfd, selectors.EVENT_READ, ("orphan", handle))

    def unwatch_orphan(self, handle: WorkerHandle) -> None:
        """Stop watching and close a worker's pidfd (when it has died, by any route).

        Why:
            A pidfd is an open fd: forgetting to close it would leak one fd per dead worker.

        Args:
            handle: the worker.
        """
        if handle.pidfd is None:
            return
        try:
            self.selector.unregister(handle.pidfd)
        except (KeyError, ValueError):
            pass  # WHY: it was never registered (the worker wasn't an orphan)
        os.close(handle.pidfd)
        handle.pidfd = None

    def _stop_launcher(self) -> None:
        """Tell the launcher to stop and, for a forkserver, reap it (≤ 2 s).

        Why:
            The forkserver is the master's child; exiting without reaping it would leave it
            running (or a zombie) after `kiln serve` returns.
        """
        launcher = self.supervisor.launcher
        launcher.stop()
        if launcher.reaps_children:
            return
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid and launcher.owns_pid(pid):
                return
            time.sleep(0.02)  # WHY: shutdown only; nothing else to serve

    def drain_worker(self, handle: WorkerHandle) -> None:
        """Read and handle every message still waiting in a dead worker's channel.

        Why:
            A process's socket data outlives the process: bytes it sent before exiting are
            still in the kernel buffer. Results among them must be counted before the worker's
            in-flight jobs are declared lost (the "reap before drain" race, ADR-034).

        Args:
            handle: the worker that has just been reaped.
        """
        if handle.conn.closed:
            return
        try:
            messages = handle.conn.read()
        except (PeerClosed, FrameError):
            return
        for msg in messages:
            self._handle_worker_message(handle, msg)

    def _handle_worker_message(self, handle: WorkerHandle, msg: dict[str, object]) -> None:
        """Route one worker message (spec/02 §4.1).

        Why:
            Workers are our own code, but a bad message must be logged and ignored, not crash the
            master (spec/02 §2.1).

        Args:
            handle: the worker.
            msg: the decoded frame.
        """
        try:
            parsed = parse_worker_message(msg)
        except BadRequest as problem:
            logsetup.event(
                log,
                logging.ERROR,
                "bad_worker_message",
                worker_id=handle.worker_id,
                error=str(problem),
            )
            return
        now = time.monotonic()
        if isinstance(parsed, Heartbeat):
            handle.last_heartbeat_mono = now
            handle.rss_bytes = parsed.rss_bytes
        elif isinstance(parsed, Ready):
            gil = bool(parsed.python.get("gil_enabled", True))
            self.supervisor.on_ready(handle, parsed.capacity, parsed.manifest, gil)
            self.dispatch_soon()
        elif isinstance(parsed, Result):
            self._on_result(handle, parsed)
        elif isinstance(parsed, Retiring):
            self.supervisor.on_retiring(handle, parsed.reason)
        elif isinstance(parsed, BootFailed):
            logsetup.event(
                log,
                logging.ERROR,
                "worker_boot_failed",
                worker_id=handle.worker_id,
                pid_worker=handle.pid,
                error_type=parsed.error.get("type"),
                error_message=parsed.error.get("message"),
                consecutive_failures=self.supervisor.boot_failures + 1,
            )

    def _on_result(self, handle: WorkerHandle, result: Result) -> None:
        """Record a job's result and free the worker's slot.

        Why:
            Frees the worker's slot and records the outcome; the attempt number makes late results
            harmless (spec/03 §4.1).

        Args:
            handle: the worker that ran it.
            result: the outcome.
        """
        now = time.monotonic()
        wall = int(time.time() * 1000)
        if handle.inflight.get(result.job_id) == result.attempt:
            del handle.inflight[result.job_id]
            handle.jobs_done += 1
        self.timers.cancel(self.job_timers.pop((result.job_id, result.attempt), None))
        if result.ok:
            job = self.jobs.succeed(
                result.job_id, result.attempt, result.value, result.duration_ms, now, wall
            )
        else:
            details = result.error or {}
            kind = "serialization" if details.get("type") == "SerializationError" else "exception"
            if result.timed_out:
                kind = "timeout"
            job = self.jobs.fail_attempt(result.job_id, result.attempt, kind, details, now, wall)
        if job is None:
            logsetup.event(
                log,
                logging.WARNING,
                "late_result_ignored",
                job_id=result.job_id,
                attempt_reported=result.attempt,
                worker_id=handle.worker_id,
            )
        else:
            logsetup.event(
                log,
                logging.DEBUG,
                "job_finished",
                job_id=job.job_id,
                state=str(job.state),
                attempt=result.attempt,
                worker_id=handle.worker_id,
                run_ms=round(result.duration_ms, 3),
            )
        self.dispatch_soon()

    # --- stats -------------------------------------------------------------------------------

    def stats(self) -> dict[str, object]:
        """Build the StatsView (spec/06 §3).

        Why:
            `kiln stats` and the load generator need one snapshot of the whole master (spec/06 §3).

        Returns:
            The stats dict.
        """
        now = time.monotonic()
        state = (
            "shutting_down"
            if self.shutting_down
            else ("reloading" if self.supervisor.reloading else "running")
        )
        workers = [
            {
                "worker_id": h.worker_id,
                "pid": h.pid,
                "gen": h.generation,
                "state": str(h.state),
                "capacity": h.capacity,
                "inflight": len(h.inflight),
                "jobs_done": h.jobs_done,
                "rss_bytes": h.rss_bytes,
                "last_heartbeat_age_s": round(now - h.last_heartbeat_mono, 2),
                "uptime_s": round(now - h.launched_mono, 2),
            }
            for h in self.workers.all()
        ]
        return {
            "server": {
                "version": __version__,
                "pid": os.getpid(),
                "uptime_s": round(now - self.started_mono, 2),
                "python": platform.python_version(),
                "start_method": self.config.start.method,
                "executor": self.config.worker.executor,
                "generation": self.supervisor.generation,
                "state": state,
            },
            "queue": {"depth": len(self.jobs.queue), "max_size": self.config.queue.max_size},
            "jobs": self.jobs.stats(),
            "workers": workers,
            "worker_deaths": dict(self.supervisor.deaths),
        }
