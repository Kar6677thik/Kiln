"""The supervisor: keep the right number of healthy workers, and know how each one died.

WHY THIS MODULE EXISTS
    Workers die: they crash, get killed by the kernel's OOM killer, hang, hit their recycling
    limit, or are replaced during a reload. The supervisor notices each death (by reaping the
    child with waitpid), works out *how* it died from the wait status, decides what happens
    to the jobs it was running, and starts a replacement, slowing down if new workers keep
    failing to boot. Every rule here comes from docs/spec/04-supervisor.md.

HOW IT FITS
    arbiter loop ──SIGCHLD──▶ reap() ──waitpid──▶ on_exit(pid, status) ──classify_exit()──▶ Death
        ──▶ in-flight jobs: jobs.fail_attempt(kind="worker_lost") ──▶ ensure_workers() ──▶ launch()
    arbiter loop ──timers──▶ check_heartbeats / check_rss / job_timeout / retire_deadline
    arbiter loop ──SIGHUP / SIGTTIN / SIGTERM──▶ reload() / scale() / begin_shutdown()

READ FIRST
    docs/spec/04-supervisor.md, docs/concepts/exit-status.md, docs/concepts/zombies-and-reaping.md,
    docs/concepts/cgroups-and-oom-killer.md

MILESTONE
    Introduced in K1.
"""

import logging
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from kiln import logsetup
from kiln.launch.fork import fork_worker
from kiln.master.connections import Connection
from kiln.master.workers import WorkerHandle, WorkerState
from kiln.protocol.messages import MsgType, Shutdown, message

if TYPE_CHECKING:
    from kiln.master.arbiter import Arbiter

log = logging.getLogger(__name__)

EXIT_BOOT_ERROR = 3
MAX_BACKOFF_S = 30.0
FAST_SHUTDOWN_GRACE_S = 2.0


@dataclass(frozen=True, slots=True)
class Death:
    """How a worker process ended, in human terms (spec/04 §4.1).

    Why:
        The raw wait status is one number; this spells out what it means and how loudly to
        log it, so the log line can say "killed by SIGKILL (suspected OOM)" instead of "9".
    """

    how: str  # retired, exited_unexpectedly, boot_failed, crashed, killed_by_master, killed
    exit_code: int | None
    signal_name: str | None
    core_dumped: bool
    suspected_oom: bool
    level: int
    hint: str

    def short(self) -> str:
        """One-phrase summary for job error messages.

        Returns:
            Such as "killed by SIGKILL (suspected OOM)".
        """
        if self.signal_name:
            extra = " (suspected OOM)" if self.suspected_oom else ""
            return f"{self.how}: {self.signal_name}{extra}"
        return f"{self.how}: exit code {self.exit_code}"


def classify_exit(wait_status: int, state: WorkerState, kill_sent: signal.Signals | None) -> Death:
    """Turn the number from waitpid() into a cause of death (spec/04 §4.1).

    Why:
        waitpid() packs "exited with code N" and "killed by signal S" into one integer.
        Telling them apart is how we tell "the task called sys.exit(1)" from "the kernel OOM
        killer SIGKILLed us" (research §2.7, the Back Market incident). A SIGKILL that Kiln
        didn't send is *suspected* OOM: only the cgroup counter can confirm it (§4.2).

    Args:
        wait_status: the second value returned by os.waitpid().
        state: the worker's state when it died (RETIRING, READY, BOOTING...).
        kill_sent: the signal the master itself sent this worker, if any.

    Returns:
        The classification.

    Example:
        >>> classify_exit(9, WorkerState.READY, None).short()   # 9 = "terminated by signal 9"
        'killed: SIGKILL (suspected OOM)'
    """
    # CONCEPT(exit-status): WIFEXITED/WEXITSTATUS read the "exited normally" half of the
    # number; WIFSIGNALED/WTERMSIG read the "killed by a signal" half.
    if os.WIFEXITED(wait_status):
        code = os.WEXITSTATUS(wait_status)
        if code == 0 and state is WorkerState.RETIRING:
            return Death("retired", 0, None, False, False, logging.INFO, "")
        if code == 0:
            return Death(
                "exited_unexpectedly",
                0,
                None,
                False,
                False,
                logging.WARNING,
                "worker exited without being asked",
            )
        if code == EXIT_BOOT_ERROR or state is WorkerState.BOOTING:
            return Death(
                "boot_failed",
                code,
                None,
                False,
                False,
                logging.ERROR,
                "the app failed to load; see the worker's log line",
            )
        return Death(
            "crashed", code, None, False, False, logging.ERROR, f"worker exited with code {code}"
        )
    sig = signal.Signals(os.WTERMSIG(wait_status))
    core = os.WCOREDUMP(wait_status)
    if kill_sent is not None and sig in (kill_sent, signal.SIGKILL, signal.SIGTERM):
        return Death("killed_by_master", None, sig.name, core, False, logging.WARNING, "")
    if sig is signal.SIGKILL:
        hint = "SIGKILL not sent by Kiln: usually the kernel OOM killer; check memory limits"
        return Death("killed", None, sig.name, core, True, logging.ERROR, hint)
    return Death(
        "killed", None, sig.name, core, False, logging.ERROR, f"worker was killed by {sig.name}"
    )


def own_cgroup_events(
    proc_root: Path = Path("/proc"), cgroup_root: Path = Path("/sys/fs/cgroup")
) -> Path | None:
    """Find this process's cgroup v2 memory.events file (spec/04 §4.2).

    Why:
        The kernel counts OOM kills per cgroup in memory.events ("oom_kill N"). Reading it
        before and after a SIGKILL turns "suspected OOM" into evidence.

    Args:
        proc_root: where /proc is.
        cgroup_root: where the cgroup v2 hierarchy is mounted.

    Returns:
        The memory.events path, or None if it can't be found.
    """
    try:
        lines = (proc_root / "self" / "cgroup").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        # WHY: cgroup v2 has exactly one line, "0::/the/path".
        if line.startswith("0::"):
            path = cgroup_root / line.removeprefix("0::").lstrip("/") / "memory.events"
            return path if path.exists() else None
    return None


def read_oom_kill_count(events_path: Path | None) -> int | None:
    """Read the oom_kill counter from a memory.events file.

    Why:
        OOM evidence is best-effort: missing cgroup files must give None, never an exception
        (spec/04 §4.2).

    Args:
        events_path: the file, or None.

    Returns:
        The count, or None if unavailable (never raises: spec/04 §4.2 "never crash because of it").
    """
    if events_path is None:
        return None
    try:
        for line in events_path.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition(" ")
            if name == "oom_kill" and value.strip().isdigit():
                return int(value)
    except OSError:
        return None
    return None


def lost_error(death: Death) -> dict[str, object]:
    """The error details recorded on a job whose worker died.

    Args:
        death: how the worker died.

    Returns:
        {"type", "message", "traceback"} for JobTable.fail_attempt.
    """
    return {"type": "WorkerLost", "message": death.short(), "traceback": ""}


def backoff_delay(consecutive_failures: int) -> float:
    """Seconds to wait before the next launch after k boot failures in a row (spec/04 §5.1).

    Why:
        A broken app would otherwise be forked hundreds of times a second.

    Args:
        consecutive_failures: boot failures since the last worker reached READY.

    Returns:
        min(0.1 × 2^(k−1), 30), or 0 when there were no failures.

    Example:
        >>> [backoff_delay(k) for k in range(5)]
        [0.0, 0.1, 0.2, 0.4, 0.8]
    """
    if consecutive_failures <= 0:
        return 0.0
    return float(min(0.1 * 2 ** (consecutive_failures - 1), MAX_BACKOFF_S))


class Supervisor:
    """Launches, watches, retires, kills and replaces workers.

    Why:
        All worker-lifecycle decisions in one class, so the arbiter loop only routes events.
    """

    def __init__(self, arb: "Arbiter") -> None:
        """Attach to the arbiter whose workers this supervises.

        Why:
            Starts with generation 0 and records the cgroup's oom_kill count, so later OOM kills can
            be confirmed.

        Args:
            arb: the arbiter (config, tables, timers, send/register helpers).
        """
        self.arb = arb
        self.target = arb.config.master.workers
        self.generation = 0
        self.reloading = False
        self.reload_started_mono = 0.0
        self.boot_failures = 0
        self.respawn_timer: int | None = None
        self.forced_kills = 0
        self.oom_events = own_cgroup_events()
        self.oom_count = read_oom_kill_count(self.oom_events)
        self.deaths = {
            "retired": 0,
            "crashed": 0,
            "killed": 0,
            "killed_by_master": 0,
            "suspected_oom": 0,
        }

    # --- launching ---------------------------------------------------------------------------

    def _current_active(self) -> int:
        """Count BOOTING/READY workers of the current generation.

        Why:
            During a reload the old generation keeps serving but doesn't count toward the
            target, so exactly `target` new workers get launched (spec/04 §5.2 step 2).

        Returns:
            The count.
        """
        return sum(
            1
            for h in self.arb.workers.all()
            if h.generation == self.generation
            and h.state in (WorkerState.BOOTING, WorkerState.READY)
        )

    def ensure_workers(self) -> None:
        """Launch workers until the current generation reaches the target (with backoff).

        Why:
            Called after every death, retirement and scale change: the single place that
            decides "start another worker now?".
        """
        if self.arb.shutting_down or self.respawn_timer is not None:
            return
        if self._current_active() >= self.target:
            return
        delay = backoff_delay(self.boot_failures)
        if delay > 0:
            self.respawn_timer = self.arb.timers.schedule(time.monotonic() + delay, "respawn")
            logsetup.event(log, logging.DEBUG, "respawn_scheduled", delay_s=delay)
            return
        while self._current_active() < self.target:
            self.launch()

    def on_respawn_timer(self) -> None:
        """The backoff delay is over: try launching again."""
        self.respawn_timer = None
        if self._current_active() < self.target and not self.arb.shutting_down:
            self.launch()
        self.ensure_workers()

    def launch(self) -> WorkerHandle:
        """Fork one new worker of the current generation.

        Why:
            One place creates workers, so every launch gets a boot deadline, a log line and a
            registered channel.

        Returns:
            Its handle (state BOOTING).
        """
        worker_id = self.arb.workers.lowest_free_id()
        started = time.monotonic()
        pid, sock = fork_worker(
            worker_id=worker_id,
            generation=self.generation,
            config=self.arb.config,
            app=self.arb.preloaded_app,
            close_in_child=self.arb.child_close_fds(),
        )
        conn = Connection(sock, "worker", self.arb.config.protocol.max_frame_bytes)
        handle = WorkerHandle(
            worker_id, self.generation, pid, conn, started, last_heartbeat_mono=started
        )
        boot_deadline = started + self.arb.config.master.boot_timeout_s
        handle.deadline_timer = self.arb.timers.schedule(boot_deadline, "boot_deadline", pid)
        self.arb.register_worker(handle)
        logsetup.event(
            log,
            logging.INFO,
            "worker_spawned",
            worker_id=worker_id,
            gen=self.generation,
            pid_worker=pid,
            start_method="fork",
            launch_ms=round((time.monotonic() - started) * 1000, 2),
        )
        return handle

    # --- worker messages ---------------------------------------------------------------------

    def on_ready(
        self, handle: WorkerHandle, capacity: int, manifest: dict[str, object], gil_enabled: bool
    ) -> None:
        """A worker finished booting: it can take jobs now.

        Why:
            A worker only gets jobs after it says READY; its manifest also tells the master which
            tasks exist (ADR-013).

        Args:
            handle: the worker.
            capacity: its slots.
            manifest: its task manifest.
            gil_enabled: from the ready message (for the log).
        """
        now = time.monotonic()
        self.arb.timers.cancel(handle.deadline_timer)
        handle.deadline_timer = None
        handle.state = WorkerState.READY
        handle.capacity = capacity
        handle.manifest = manifest
        handle.last_heartbeat_mono = now
        self.boot_failures = 0
        if handle.generation == self.generation:
            self.arb.set_manifest(manifest, self.generation)
        logsetup.event(
            log,
            logging.INFO,
            "worker_ready",
            worker_id=handle.worker_id,
            gen=handle.generation,
            pid_worker=handle.pid,
            capacity=capacity,
            executor="sync",
            boot_ms=round((now - handle.launched_mono) * 1000, 2),
            gil_enabled=gil_enabled,
            tasks=len(manifest.get("tasks", {})),  # type: ignore[arg-type]  # WHY: manifest["tasks"] is a dict
        )
        if self.reloading:
            self._retire_one_old()

    def on_retiring(self, handle: WorkerHandle, reason: str) -> None:
        """The worker decided to leave (max_jobs or max_rss): stop sending it work.

        Why:
            The worker chose to leave (max_jobs, max_rss, SIGTERM); we stop sending it work and
            complete the handshake (ADR-034).

        Args:
            handle: the worker.
            reason: "max_jobs" or "max_rss".
        """
        if reason == "max_rss":
            logsetup.event(
                log,
                logging.WARNING,
                "worker_over_memory",
                worker_id=handle.worker_id,
                metric=self.arb.config.worker.memory_metric,
                value_mb=round((handle.rss_bytes or 0) / 2**20, 1),
                limit_mb=self.arb.config.worker.max_rss_mb,
                detected_by="worker",
            )
        # WHY: the worker keeps serving until our `shutdown` arrives (the retirement
        # handshake, ADR-034), so no `run` already on its way to it can be lost.
        self._mark_retiring(handle, reason, send_shutdown=True)

    # --- retiring and killing ----------------------------------------------------------------

    def _mark_retiring(self, handle: WorkerHandle, reason: str, send_shutdown: bool) -> None:
        """Move a worker to RETIRING, start its grace deadline, and launch a replacement.

        Why:
            Shared by every way a worker leaves gracefully: no new jobs, a grace deadline, and a
            replacement launched.

        Args:
            handle: the worker.
            reason: why (for the log).
            send_shutdown: whether to send `shutdown{graceful}` (False if it retired itself).
        """
        if handle.state is WorkerState.EXITED:
            return
        if handle.state is WorkerState.RETIRING:
            # WHY: already retiring (we asked first); the worker's own `retiring` crossed ours.
            return
        handle.state = WorkerState.RETIRING
        handle.retire_reason = reason
        if send_shutdown:
            self.arb.send(handle.conn, message(MsgType.SHUTDOWN, Shutdown(graceful=True)))
        grace = self.arb.config.master.graceful_timeout_s
        self.arb.timers.cancel(handle.deadline_timer)
        handle.deadline_timer = self.arb.timers.schedule(
            time.monotonic() + grace, "retire_deadline", handle.pid
        )
        logsetup.event(
            log,
            logging.INFO,
            "worker_retiring",
            worker_id=handle.worker_id,
            pid_worker=handle.pid,
            reason=reason,
            inflight=len(handle.inflight),
            deadline_s=grace,
        )
        self.ensure_workers()

    def retire(self, handle: WorkerHandle, reason: str) -> None:
        """Ask a worker to leave gracefully: finish in-flight jobs, then exit 0 (spec/04 §5.2).

        Args:
            handle: the worker.
            reason: such as "reload", "scale_down", "shutdown", "max_rss".
        """
        self._mark_retiring(handle, reason, send_shutdown=True)

    def kill(self, handle: WorkerHandle, sig: signal.Signals, reason: str) -> None:
        """Send a signal to a worker and remember that *we* sent it.

        Why:
            kill_sent is how classify_exit tells "killed by Kiln" from "killed by the kernel".

        Args:
            handle: the worker.
            sig: the signal (SIGKILL or SIGTERM).
            reason: why (job_timeout, heartbeat_timeout, boot_timeout, grace_expired, quit).
        """
        handle.kill_sent = sig
        handle.kill_reason = reason
        logsetup.event(
            log,
            logging.WARNING,
            "worker_kill_sent",
            worker_id=handle.worker_id,
            pid_worker=handle.pid,
            signal=sig.name,
            reason=reason,
        )
        try:
            # SYSCALL: os.kill → kill(2): send a signal to a process by pid.
            os.kill(handle.pid, sig)
        except ProcessLookupError:
            pass  # WHY: ESRCH: it already died; reaping will report how

    def _retire_one_old(self) -> None:
        """During a reload: retire the old-generation READY worker with the fewest jobs.

        Why:
            One old worker leaves for each new one that becomes READY, so capacity never drops
            below the target (spec/04 §5.2 step 3).
        """
        old = [
            h
            for h in self.arb.workers.all()
            if h.generation < self.generation and h.state is WorkerState.READY
        ]
        if not old:
            return
        old.sort(key=_fewest_inflight)
        self.retire(old[0], "reload")

    # --- deaths ------------------------------------------------------------------------------

    def reap(self) -> None:
        """Collect every dead child (spec/04 §4) and handle each death.

        Why:
            A dead child stays a zombie until its parent calls waitpid. One SIGCHLD can stand
            for several deaths (signals don't queue), so we loop until nothing is left
            (docs/concepts/zombies-and-reaping.md).
        """
        while True:
            try:
                # SYSCALL: waitpid(-1, WNOHANG) → wait4(2): any child; don't block.
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return  # WHY: ECHILD: we have no children at all
            if pid == 0:
                return  # WHY: children exist, but none has exited
            self.on_exit(pid, status)

    def on_exit(self, pid: int, wait_status: int) -> None:
        """Handle one worker's death: classify, resolve its jobs, maybe replace it.

        Why:
            Every worker death, expected or not, ends here: classify it, settle its jobs, and keep
            the worker count up.

        Args:
            pid: the dead process.
            wait_status: its status from waitpid.
        """
        handle = self.arb.workers.by_pid.get(pid)
        if handle is None:
            return
        # GOTCHA ("reap before drain"): the worker may have sent its last results just before
        # exiting, and this loop pass may handle SIGCHLD before reading its socket. Read what's
        # left in the channel FIRST, or finished jobs would be wrongly marked LOST (ADR-034).
        self.arb.drain_worker(handle)
        death = classify_exit(wait_status, handle.state, handle.kill_sent)
        was_booting = handle.state is WorkerState.BOOTING
        evidence = self._oom_evidence(death)
        self._count_death(death)
        logsetup.event(
            log,
            death.level,
            "worker_died",
            worker_id=handle.worker_id,
            pid_worker=pid,
            gen=handle.generation,
            how=death.how,
            exit_code=death.exit_code,
            signal=death.signal_name,
            core_dumped=death.core_dumped,
            suspected_oom=death.suspected_oom,
            oom_confirmed=evidence[0],
            oom_evidence=evidence[1],
            kill_reason=handle.kill_reason,
            inflight=len(handle.inflight),
            jobs_done=handle.jobs_done,
            uptime_s=round(time.monotonic() - handle.launched_mono, 2),
            hint=death.hint,
        )
        self._resolve_inflight(handle, death)
        self.arb.timers.cancel(handle.deadline_timer)
        handle.state = WorkerState.EXITED
        self.arb.unregister_worker(handle)
        if was_booting:
            self._on_boot_failure(death)
        self._check_reload_finished()
        self.ensure_workers()
        self.arb.dispatch_soon()

    def _oom_evidence(self, death: Death) -> tuple[bool, str]:
        """Compare the cgroup's oom_kill counter with the last reading (spec/04 §4.2).

        Why:
            A SIGKILL we didn't send is only *suspected* OOM; the cgroup counter turns suspicion
            into evidence (spec/04 §4.2).

        Args:
            death: the classification.

        Returns:
            (confirmed, human-readable evidence).
        """
        if not death.suspected_oom:
            return False, ""
        before = self.oom_count
        after = read_oom_kill_count(self.oom_events)
        self.oom_count = after
        if before is None or after is None:
            return False, "unavailable"
        if after > before:
            return True, f"cgroup memory.events oom_kill {before}→{after}"
        return False, f"cgroup memory.events oom_kill unchanged ({after})"

    def _count_death(self, death: Death) -> None:
        """Update the worker_deaths counters of StatsView (spec/06 §3).

        Why:
            The worker_deaths counters in StatsView let you spot crash patterns without reading
            logs.

        Args:
            death: the classification.
        """
        bucket = (
            "crashed"
            if death.how in ("crashed", "boot_failed", "exited_unexpectedly")
            else death.how
        )
        self.deaths[bucket] = self.deaths.get(bucket, 0) + 1
        if death.suspected_oom:
            self.deaths["suspected_oom"] += 1

    def _resolve_inflight(self, handle: WorkerHandle, death: Death) -> None:
        """Every job still running on the dead worker becomes worker_lost (maybe retried).

        Why:
            Jobs on a dead worker must not stay RUNNING forever; they become worker_lost and maybe
            retry (spec/03 §4.1).

        Args:
            handle: the dead worker.
            death: how it died (included in each job's error message).
        """
        now = time.monotonic()
        wall = int(time.time() * 1000)
        for job_id, attempt in list(handle.inflight.items()):
            self.arb.timers.cancel(self.arb.job_timers.pop((job_id, attempt), None))
            job = self.arb.jobs.fail_attempt(
                job_id,
                attempt,
                "worker_lost",
                lost_error(death),
                now,
                wall,
            )
            if job is not None:
                logsetup.event(
                    log,
                    logging.ERROR,
                    "job_lost",
                    job_id=job_id,
                    task=job.task,
                    attempt=attempt,
                    worker_id=handle.worker_id,
                    death=death.short(),
                    will_retry=str(job.state) == "QUEUED",
                )
        handle.inflight.clear()

    def _on_boot_failure(self, death: Death) -> None:
        """Count a boot failure; give up (or abandon a reload) after the limit (spec/04 §5.1).

        Why:
            Without a limit, an app that can't import would be forked forever; spec/04 §5.1 says
            give up after N tries.

        Args:
            death: how the booting worker died.
        """
        self.boot_failures += 1
        limit = self.arb.config.master.boot_failure_limit
        logsetup.event(
            log,
            logging.ERROR,
            "worker_boot_failed",
            consecutive_failures=self.boot_failures,
            next_attempt_in_s=backoff_delay(self.boot_failures),
            error_message=death.short(),
        )
        if self.boot_failures < limit:
            return
        old_ready = [
            h
            for h in self.arb.workers.all()
            if h.generation < self.generation and h.state is WorkerState.READY
        ]
        if self.reloading and old_ready:
            # WHY: spec/04 §5.2 step 7: keep serving with the old generation instead of exiting.
            logsetup.event(
                log,
                logging.CRITICAL,
                "reload_failed",
                gen_new=self.generation,
                reason="boot_failure_limit",
            )
            self.generation -= 1
            self.reloading = False
            self.boot_failures = 0
            return
        logsetup.event(
            log,
            logging.CRITICAL,
            "boot_failure_limit_reached",
            consecutive_failures=self.boot_failures,
            last_error=death.short(),
        )
        self.arb.begin_shutdown("give_up")

    # --- timers ------------------------------------------------------------------------------

    def on_deadline(self, kind: str, pid: int) -> None:
        """A boot or retire deadline passed: kill the worker (spec/04 §5.2 step 5, §5.6).

        Why:
            A worker that never boots, or never finishes retiring, must not hang the master; the
            deadline forces it out.

        Args:
            kind: "boot_deadline" or "retire_deadline".
            pid: the worker's pid.
        """
        handle = self.arb.workers.by_pid.get(pid)
        if handle is None:
            return
        if kind == "boot_deadline" and handle.state is WorkerState.BOOTING:
            logsetup.event(
                log,
                logging.ERROR,
                "worker_boot_timeout",
                worker_id=handle.worker_id,
                pid_worker=pid,
                boot_timeout_s=self.arb.config.master.boot_timeout_s,
            )
            self.kill(handle, signal.SIGKILL, "boot_timeout")
        elif kind == "retire_deadline" and handle.state is WorkerState.RETIRING:
            self.forced_kills += 1
            self.kill(handle, signal.SIGKILL, "grace_expired")

    def on_job_timeout(self, job_id: str, attempt: int) -> None:
        """A job ran past its timeout: fail (or retry) it and kill its worker (spec/04 §5.3).

        Why:
            Python can't stop one running function safely, so the only way to free the slot
            is to kill the worker process. Other jobs on it become worker_lost ("collateral").

        Args:
            job_id: the job.
            attempt: the attempt that timed out.
        """
        self.arb.job_timers.pop((job_id, attempt), None)
        handle = next(
            (h for h in self.arb.workers.all() if h.inflight.get(job_id) == attempt), None
        )
        if handle is None:
            return
        job = self.arb.jobs.jobs.get(job_id)
        timeout_s = job.timeout_s if job else None
        now = time.monotonic()
        error: dict[str, object] = {
            "type": "Timeout",
            "message": f"job exceeded timeout_s={timeout_s}",
            "traceback": "",
        }
        result = self.arb.jobs.fail_attempt(
            job_id, attempt, "timeout", error, now, int(time.time() * 1000)
        )
        handle.inflight.pop(job_id, None)
        if result is not None:
            logsetup.event(
                log,
                logging.WARNING,
                "job_timeout",
                job_id=job_id,
                task=result.task,
                attempt=attempt,
                worker_id=handle.worker_id,
                timeout_s=timeout_s,
                will_retry=str(result.state) == "QUEUED",
            )
        self.kill(handle, signal.SIGKILL, "job_timeout")

    def check_heartbeats(self) -> None:
        """Kill workers that have been silent longer than heartbeat_timeout_s (spec/04 §5.4).

        Why:
            A hung worker sends nothing, so silence is the only symptom; checking it is how hangs
            get noticed (spec/04 §5.4).
        """
        now = time.monotonic()
        timeout = self.arb.config.worker.heartbeat_timeout_s
        for handle in self.arb.workers.all():
            alive = (
                handle.state in (WorkerState.READY, WorkerState.RETIRING)
                and handle.kill_sent is None
            )
            silence = now - handle.last_heartbeat_mono
            if alive and silence > timeout:
                logsetup.event(
                    log,
                    logging.ERROR,
                    "worker_heartbeat_timeout",
                    worker_id=handle.worker_id,
                    pid_worker=handle.pid,
                    silence_s=round(silence, 2),
                )
                self.kill(handle, signal.SIGKILL, "heartbeat_timeout")

    def check_rss(self) -> None:
        """Retire READY workers whose last reported RSS is over max_rss_mb (spec/04 §5.5).

        Why:
            A worker stuck in one long job can't check its own RSS, so the master checks the
            heartbeat values too.
        """
        limit = self.arb.config.worker.max_rss_mb
        if limit <= 0:
            return
        for handle in self.arb.workers.all():
            if handle.state is WorkerState.READY and (handle.rss_bytes or 0) > limit * 2**20:
                logsetup.event(
                    log,
                    logging.WARNING,
                    "worker_over_memory",
                    worker_id=handle.worker_id,
                    metric="rss",
                    value_mb=round((handle.rss_bytes or 0) / 2**20, 1),
                    limit_mb=limit,
                    detected_by="master",
                )
                self.retire(handle, "max_rss")

    # --- reload, scale, shutdown -------------------------------------------------------------

    def reload(self) -> None:
        """SIGHUP: start a new generation; old workers retire as new ones become READY.

        Why:
            SIGHUP replaces every worker without dropping jobs (spec/04 §5.2): new ones start first,
            old ones retire after.
        """
        if self.arb.shutting_down:
            return
        old = self.generation
        self.generation += 1
        self.reloading = True
        self.reload_started_mono = time.monotonic()
        self.target = self.arb.config.master.workers
        logsetup.event(
            log,
            logging.INFO,
            "reload_started",
            gen_old=old,
            gen_new=self.generation,
            workers=self.target,
        )
        self.ensure_workers()

    def _check_reload_finished(self) -> None:
        """Log reload_finished once no older-generation worker remains.

        Why:
            A reload is done only when the last old worker has exited; logging that moment shows how
            long it took.
        """
        if not self.reloading:
            return
        if any(h.generation < self.generation for h in self.arb.workers.all()):
            return
        self.reloading = False
        elapsed = round((time.monotonic() - self.reload_started_mono) * 1000, 1)
        logsetup.event(
            log,
            logging.INFO,
            "reload_finished",
            gen=self.generation,
            duration_ms=elapsed,
            forced_kills=self.forced_kills,
        )

    def scale(self, delta: int) -> None:
        """SIGTTIN (+1) / SIGTTOU (−1): change the target worker count (minimum 1).

        Why:
            Lets you change capacity without a restart; scaling down retires a worker gracefully
            rather than killing it.

        Args:
            delta: +1 or −1.
        """
        old = self.target
        self.target = max(1, self.target + delta)
        logsetup.event(log, logging.INFO, "scale_changed", old=old, new=self.target)
        if self.target > old:
            self.ensure_workers()
            return
        ready = [
            h
            for h in self.arb.workers.all()
            if h.generation == self.generation and h.state is WorkerState.READY
        ]
        if self.target < old and ready:
            # WHY: spec/04 §3: the newest READY worker retires (highest id as a tie-break).
            ready.sort(key=_newest_first)
            self.retire(ready[0], "scale_down")

    def stop_all(self, mode: str) -> None:
        """Begin stopping every worker for a shutdown (spec/04 §5.6).

        Why:
            The three shutdown modes differ only in how workers are stopped: a message, SIGTERM or
            SIGKILL.

        Args:
            mode: "graceful"/"give_up" (shutdown message), "fast" (SIGTERM) or
                "immediate" (SIGKILL).
        """
        for handle in self.arb.workers.all():
            if mode in ("graceful", "give_up"):
                self.retire(handle, "shutdown")
            elif mode == "fast":
                self.kill(handle, signal.SIGTERM, "fast_shutdown")
            else:
                self.kill(handle, signal.SIGKILL, "quit")

    def force_kill_remaining(self) -> None:
        """Shutdown deadline passed: SIGKILL every worker still alive."""
        for handle in self.arb.workers.all():
            if handle.kill_sent is not signal.SIGKILL:
                self.forced_kills += 1
                self.kill(handle, signal.SIGKILL, "grace_expired")


def _fewest_inflight(handle: WorkerHandle) -> tuple[int, int]:
    """Sort key: fewest in-flight jobs first, then lowest id.

    Args:
        handle: a worker.

    Returns:
        The key.
    """
    return (len(handle.inflight), handle.worker_id)


def _newest_first(handle: WorkerHandle) -> tuple[float, int]:
    """Sort key: most recently launched first.

    Args:
        handle: a worker.

    Returns:
        The key.
    """
    return (-handle.launched_mono, -handle.worker_id)
