"""The worker's main function: boot, say "ready", run jobs until told to stop, exit cleanly.

WHY THIS MODULE EXISTS
    A worker has a short, strict life (docs/spec/04-supervisor.md §1): set up signals and
    logging, load the app, report `ready` with the task manifest, then loop: receive a `run`,
    execute it, send the `result`, check whether it's time to retire. Every start method
    (fork in K1; spawn and forkserver in K2) ends up calling run_worker().

HOW IT FITS
    launch/fork.py (child side) ──run_worker(...)──▶ exit code ──finalize_and_exit(code)──▶ os._exit
    run_worker ◀──run / shutdown── channel ──ready / heartbeat / result / retiring──▶ master

READ FIRST
    docs/concepts/fork.md (why a forked child must end with os._exit), docs/concepts/signals.md,
    docs/spec/04-supervisor.md §3.1 and §5.5, docs/python/os-module.md

MILESTONE
    Introduced in K1.
"""

import logging
import os
import platform
import random
import signal
import socket
import sys
import sysconfig
from types import FrameType
from typing import NoReturn

from kiln import logsetup
from kiln.app import App, AppLoadError, load_app
from kiln.config import Config
from kiln.labs import tasks as lab_tasks
from kiln.memory.procfs import read_status
from kiln.protocol.messages import (
    BadRequest,
    BootFailed,
    MsgType,
    Ready,
    Result,
    Retiring,
    Run,
    Shutdown,
    message,
    parse_master_message,
)
from kiln.worker.channel import Channel, ChannelClosed
from kiln.worker.executors.sync import SyncExecutor
from kiln.worker.heartbeat import HeartbeatThread

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_BOOT_ERROR = 3  # spec/04 §5.6: the master recognises this as "the app failed to load"
OOM_SCORE_ADJ = 500  # ADR-029
POLL_S = 0.5  # how often the receive loop wakes up to check the retire flag


class WorkerState:
    """The worker's own counters and flags (one per process).

    Why:
        Signal handlers and the heartbeat thread need to read or set these; one small object
        is clearer than module globals (code-style §6).
    """

    def __init__(self) -> None:
        """Start with nothing done."""
        self.jobs_done = 0
        self.retiring = False
        self.retire_reason = ""
        self.announced = False  # True once `retiring` has been sent to the master

    def on_sigterm(self, signum: int, frame: FrameType | None) -> None:
        """SIGTERM handler: finish the current job, then exit (spec/04 §3.1).

        Why:
            Like every handler, it only sets a flag; the receive loop acts on it.

        Args:
            signum: the signal number.
            frame: the interrupted frame (unused).
        """
        self.retiring = True
        self.retire_reason = "sigterm"


def _setup_signals(state: WorkerState) -> None:
    """Give the worker its own signal behaviour (spec/04 §3.1), replacing the master's.

    Why:
        After fork() the child inherits the master's handlers AND the master's wakeup fd. Left
        alone, a signal to the worker would write into the *master's* wakeup pipe.

    Args:
        state: the worker's state (SIGTERM sets its retire flag).
    """
    # CONCEPT(signals): set_wakeup_fd(-1) disconnects the inherited wakeup socket.
    signal.set_wakeup_fd(-1)
    # WHY (ADR-024): Ctrl-C reaches the whole process group; only the master decides.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    for signum in (signal.SIGHUP, signal.SIGTTIN, signal.SIGTTOU, signal.SIGUSR1):
        signal.signal(signum, signal.SIG_IGN)  # master-only signals mean nothing here
    for signum in (signal.SIGQUIT, signal.SIGCHLD):
        signal.signal(signum, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, state.on_sigterm)


def _raise_oom_score() -> None:
    """Ask the kernel to prefer killing this worker over the master when memory runs out.

    Why:
        ADR-029: the supervisor must survive an OOM kill to report it. Raising your own
        oom_score_adj needs no privileges; failure is harmless.
    """
    try:
        with open("/proc/self/oom_score_adj", "w", encoding="ascii") as handle:  # noqa: PTH123  # WHY: a procfs control file, written once
            handle.write(str(OOM_SCORE_ADJ))
    except OSError as error:
        logsetup.event(log, logging.DEBUG, "oom_score_adj_failed", error=str(error))


def _python_info() -> dict[str, object]:
    """Describe this interpreter for the `ready` message.

    Why:
        The master logs and reports which Python and build each worker runs, which matters on 3.15t
        (K4).

    Returns:
        version, implementation, gil_enabled, free_threaded_build.
    """
    probe = getattr(sys, "_is_gil_enabled", None)
    return {
        "version": platform.python_version(),
        "implementation": sys.implementation.name,
        "gil_enabled": True if probe is None else bool(probe()),
        "free_threaded_build": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
    }


def _over_memory(config: Config) -> bool:
    """Check worker.max_rss_mb after a job (spec/04 §5.5).

    Why:
        Leaky tasks make a worker grow; retiring it before it gets too big is cheaper than an OOM
        kill (spec/04 §5.5).

    Args:
        config: the configuration.

    Returns:
        True if the worker's RSS exceeds the limit.
    """
    limit = config.worker.max_rss_mb
    if limit <= 0:
        return False
    try:
        return read_status("self").vm_rss > limit * 1024 * 1024
    except OSError:
        return False


def _check_recycling(config: Config, state: WorkerState, max_jobs_limit: int) -> None:
    """After each job: decide whether this worker should retire (max_jobs or max_rss).

    Why:
        Recycling decisions are made after each job, the only safe moment between jobs (spec/04
        §5.5).

    Args:
        config: the configuration.
        state: the worker's state (retiring flag set here).
        max_jobs_limit: max_jobs plus this worker's jitter, or 0 for unlimited.
    """
    if state.retiring:
        return
    if max_jobs_limit and state.jobs_done >= max_jobs_limit:
        state.retiring, state.retire_reason = True, "max_jobs"
    elif _over_memory(config):
        state.retiring, state.retire_reason = True, "max_rss"


def _boot(channel: Channel, config: Config, app: App | None) -> App | None:
    """Load the app (unless it was preloaded before fork) and register lab tasks.

    Why:
        A preloaded app is inherited through fork; otherwise the worker imports it and reports
        failure as boot_failed.

    Args:
        channel: for reporting boot_failed.
        config: the configuration.
        app: the preloaded app, or None.

    Returns:
        The app, or None if loading failed (boot_failed has been sent).
    """
    if app is None:
        try:
            app = load_app(config.app.spec, config.app.pythonpath)
        except AppLoadError as error:
            failure = BootFailed(
                error={"type": "AppLoadError", "message": str(error), "traceback": ""}
            )
            channel.send(message(MsgType.BOOT_FAILED, failure))
            return None
    if config.labs.enabled and "labs.echo" not in app.tasks():
        lab_tasks.register(app)
    return app


def run_worker(
    *, channel: socket.socket, worker_id: int, generation: int, config: Config, app: App | None
) -> int:
    """The whole life of a worker. Returns the exit code; never calls exit itself.

    Why:
        Returning the code (instead of exiting) lets tests run a worker inside a thread, and
        lets each start method choose how to exit (fork: os._exit; spawn: sys.exit in K2).

    Args:
        channel: the worker's end of its socketpair with the master.
        worker_id: this worker's id.
        generation: the reload generation it belongs to.
        config: the configuration.
        app: the app preloaded by the master before fork, or None to import it here.

    Returns:
        0 for a normal exit, 3 if the app failed to load.
    """
    state = WorkerState()
    _setup_signals(state)
    logsetup.configure("worker", config.log.level, config.log.format, config.log.file, worker_id)
    _raise_oom_score()
    chan = Channel(channel, config.protocol.max_frame_bytes)
    loaded = _boot(chan, config, app)
    if loaded is None:
        return EXIT_BOOT_ERROR
    executor = SyncExecutor(loaded)
    # WHY: jitter spreads recycling so workers started together don't all restart together.
    # WHY (noqa S311): random isn't for cryptography, and spreading restarts doesn't need it.
    jitter = random.randint(0, config.worker.max_jobs_jitter)  # noqa: S311
    max_jobs_limit = config.worker.max_jobs + jitter if config.worker.max_jobs else 0
    # PY: a lambda is a one-line function; the heartbeat calls it to read live progress.
    heartbeat = HeartbeatThread(
        chan,
        config.worker.heartbeat_interval_s,
        lambda: (state.jobs_done, executor.inflight()),
    )
    heartbeat.start()
    ready = Ready(
        worker_id=worker_id,
        pid=os.getpid(),
        generation=generation,
        capacity=executor.capacity,
        executor="sync",
        manifest=loaded.manifest(config.jobs),
        python=_python_info(),
    )
    try:
        chan.send(message(MsgType.READY, ready))
        logsetup.event(
            log,
            logging.INFO,
            "worker_booting",
            gen=generation,
            start_method=config.start.method,
            executor="sync",
        )
        return _receive_loop(chan, executor, config, state, max_jobs_limit)
    except ChannelClosed:
        # WHY: workers don't outlive their master (K1 spec §5.8 step 9).
        logsetup.event(log, logging.WARNING, "master_gone", jobs_done=state.jobs_done)
        return EXIT_OK
    finally:
        heartbeat.stop()
        logsetup.event(log, logging.INFO, "worker_exiting", jobs_done=state.jobs_done, exit_code=0)


def _receive_loop(
    chan: Channel, executor: SyncExecutor, config: Config, state: WorkerState, max_jobs_limit: int
) -> int:
    """Receive and handle master messages until it's time to exit.

    Why:
        The worker's main loop: run jobs, honour shutdown, and complete the retirement handshake
        (ADR-034).

    Args:
        chan: the channel.
        executor: runs the jobs.
        config: the configuration.
        state: counters and flags.
        max_jobs_limit: retire after this many jobs (0 = never).

    Returns:
        The exit code (0).
    """
    # WHY (the retirement handshake, ADR-034): a worker that decides to leave (max_jobs,
    # max_rss, SIGTERM) first *announces* it with `retiring` and keeps serving. The master
    # answers with `shutdown`. Messages on one channel arrive in order, so any `run` the master
    # sent before it read our announcement arrives BEFORE the shutdown and still gets served.
    # Exiting right after the announcement would silently lose such a job.
    shutdown_received = False

    def finished(result: Result) -> None:
        """A job finished: count it, decide about recycling, THEN send its result.

        Why:
            If the result went first, the master would see a free slot and could send the
            next `run` before our `retiring` arrived, so a worker with max_jobs=15 could end
            up doing 16 (found by test_max_jobs_with_jitter_recycles_between_10_and_15 under
            forkserver). Announcing before the result closes that window: the master reads
            `retiring` first and never refills the slot.

        Args:
            result: the job's result.
        """
        state.jobs_done += 1
        _check_recycling(config, state, max_jobs_limit)
        if not shutdown_received:
            _announce(chan, state)
        _send_result(chan, result)

    while True:
        if not shutdown_received:
            _announce(chan, state)  # WHY: SIGTERM can set `retiring` between jobs
        if shutdown_received and executor.inflight() == 0:
            return EXIT_OK
        raw = chan.receive(timeout_s=POLL_S)
        if raw is None:
            continue
        try:
            parsed = parse_master_message(raw)
        except BadRequest as error:
            logsetup.event(log, logging.ERROR, "bad_master_message", error=str(error))
            continue
        if isinstance(parsed, Shutdown):
            shutdown_received = True
            continue
        if isinstance(parsed, Run):
            executor.submit(parsed, finished)


def _announce(chan: Channel, state: WorkerState) -> None:
    """Send `retiring` once, if this worker has decided to leave.

    Why:
        The master must hear about a retirement exactly once, and as early as possible, so
        it stops sending this worker work (the retirement handshake, ADR-034).

    Args:
        chan: the channel.
        state: the worker's state.
    """
    if state.retiring and not state.announced:
        chan.send(message(MsgType.RETIRING, Retiring(reason=state.retire_reason)))
        state.announced = True


def _send_result(chan: Channel, result: Result) -> None:
    """Send one job's result to the master.

    Args:
        chan: the channel.
        result: the result.
    """
    chan.send(message(MsgType.RESULT, result))


def finalize_and_exit(code: int) -> NoReturn:
    """End a *forked* worker process: flush our own output, then os._exit(code).

    Why:
        ADR-028: a forked child holds a copy of the master's whole interpreter. Returning or
        calling sys.exit() would run the master's atexit handlers, flush the master's copied
        buffers (printing things twice) and unwind into the master's code. os._exit() ends the
        process immediately. We flush only what is *ours* first: our log lines and stdio.
        It also skips the interpreter's final garbage collection, which is exactly what
        Instagram's `atexit.register(os._exit, 0)` trick achieves (K3, errata E-16).

    Args:
        code: the exit code for waitpid.
    """
    try:
        logging.shutdown()
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        # CONCEPT(fork): the one correct way out of a forked child.
        os._exit(code)
