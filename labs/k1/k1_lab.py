"""K1 lab driver: the experiments of docs/milestones/K1-supervisor.md §7, one subcommand each.

WHY THIS MODULE EXISTS
    A lab must be repeatable: same commands, same measurements, results saved as
    ExperimentRecords (ADR-022), so the numbers in docs/labs/K1-supervisor.md can be checked
    and re-run by anyone. This script starts real Kiln servers, drives them, and writes records.

HOW IT FITS
    ./scripts/dev.sh lab k1-deaths    → deaths     (a) death classification
    ./scripts/dev.sh lab k1-oom       → oom        (a) a real kernel OOM kill in a 512 MiB cgroup
    ./scripts/dev.sh lab k1-reload    → reload     (b) zero-drop reload under load (SC-1, SC-2)
    ./scripts/dev.sh lab k1-ceiling   → ceiling    (c) the master's throughput ceiling
    ./scripts/dev.sh lab k1-heartbeat → heartbeat  (d) CPU loop vs GIL-holding call
    ./scripts/dev.sh lab k1-gilprobe  → gilprobe   gate G1.6 (does PyDLL really hold the GIL?)
    Each writes results/K1/<experiment>/<run_id>.json and prints a summary table.

READ FIRST
    docs/labs/K1-supervisor.md, docs/spec/10-measurement-methodology.md

MILESTONE
    Introduced in K1.
"""

import argparse
import ctypes
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from kiln.bench import records
from kiln.bench.load import LoadOptions, run_load
from kiln.client import KilnClient

REPO = Path(__file__).resolve().parents[2]
FIELD = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')


class Server:
    """A `kiln serve` subprocess for one experiment (its own process group).

    Why:
        Every experiment needs a fresh server with specific settings, and must never leave
        stray processes behind, even if the experiment fails halfway.
    """

    def __init__(
        self, workdir: Path, workers: int, env: dict[str, str], prefix: list[str] | None = None
    ) -> None:
        """Start the server and wait until it listens.

        Why:
            Experiments must not start measuring before the master is ready (ADR-013).

        Args:
            workdir: folder for the log and pidfile.
            workers: worker count.
            env: extra KILN_* settings.
            prefix: a command to run kiln under (systemd-run for the OOM lab).
        """
        workdir.mkdir(parents=True, exist_ok=True)
        self.log_path = workdir / "kiln.log"
        self.pidfile = workdir / "kiln.pid"
        command = [
            *(prefix or []),
            sys.executable,
            "-m",
            "kiln",
            "serve",
            "examples.demo:app",
            "--labs",
            "--workers",
            str(workers),
            "--bind",
            "127.0.0.1:0",
            "--pidfile",
            str(self.pidfile),
        ]
        self._log = self.log_path.open("w")
        self.proc = subprocess.Popen(
            command,
            cwd=REPO,
            stderr=self._log,
            stdout=subprocess.DEVNULL,
            env={**os.environ, **env},
            start_new_session=True,
        )
        self.port = 0
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not self.port:
            for event in self.events("listening"):
                self.port = int(event["bind"].rsplit(":", 1)[1])
            time.sleep(0.05)
        if not self.port:
            raise RuntimeError(f"server did not start; see {self.log_path}")
        while not self.pidfile.exists():
            time.sleep(0.05)
        self.master_pid = int(self.pidfile.read_text().strip())

    def events(self, name: str) -> list[dict[str, str]]:
        """Parse every log line with this event name.

        Why:
            The lab reads Kiln's own structured events (spec/06) instead of guessing from timing.

        Args:
            name: the event name.

        Returns:
            The parsed lines.
        """
        found = []
        for line in self.log_path.read_text(errors="replace").splitlines():
            if f"event={name} " in line + " ":
                fields = {}
                for key, value in FIELD.findall(line):
                    fields[key] = value.strip('"')
                found.append(fields)
        return found

    def client(self) -> KilnClient:
        """A connected client.

        Returns:
            The client.
        """
        client = KilnClient("127.0.0.1", self.port, timeout_s=120.0)
        client.connect()
        return client

    def stop(self) -> int:
        """Stop gracefully (SIGTERM to the master), then make sure the group is gone.

        Why:
            A lab must never leave stray workers behind to disturb the next experiment.

        Returns:
            The exit code (or -9 if it had to be killed).
        """
        try:
            os.kill(self.master_pid, signal.SIGTERM)
            code = self.proc.wait(timeout=60)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            code = -9
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self._log.close()
        return code


def save(record: dict[str, object], results: Path) -> Path:
    """Write a record and print where.

    Args:
        record: the record.
        results: the results folder.

    Returns:
        The path written.
    """
    path = records.write_record(record, results)
    print(f"  record: {path.relative_to(REPO) if path.is_relative_to(REPO) else path}")
    return path


def lab_deaths(args: argparse.Namespace) -> None:
    """(a) Kill a worker in each way and record how Kiln classified it.

    Why:
        Shows that the wait status tells "exit code 1" apart from SIGSEGV, SIGABRT and an
        unexplained SIGKILL (suspected OOM), docs/spec/04-supervisor.md §4.1.

    Args:
        args: parsed arguments (results folder).
    """
    server = Server(args.work / "deaths", 2, {"KILN_LOG__LEVEL": "INFO"})
    client = server.client()
    rows = []
    for how in ("exit1", "segv", "abort", "sigkill_self"):
        before = len(server.events("worker_died"))
        job = client.wait(client.submit("labs.crash", kwargs={"how": how}), timeout_s=30)["job"]
        while len(server.events("worker_died")) <= before:
            time.sleep(0.05)
        death = server.events("worker_died")[before]
        rows.append(
            {
                "how": how,
                "classified": death["how"],
                "exit_code": death["exit_code"],
                "signal": death["signal"],
                "suspected_oom": death["suspected_oom"],
                "oom_confirmed": death["oom_confirmed"],
                "oom_evidence": death.get("oom_evidence", ""),
                "job_state": job["state"],
            }
        )  # type: ignore[index]
    server.stop()
    record = records.new_record("K1", "deaths", "crash-modes", {"workers": 2}, 0)
    records.add_metric(record, "modes_tested", "count", value=len(rows))
    record["notes"] = json.dumps(rows)
    save(record, args.results)
    for row in rows:
        print("  " + " | ".join(f"{k}={v}" for k, v in row.items()))


def lab_oom(args: argparse.Namespace) -> None:
    """(a) A real kernel OOM kill inside a 512 MiB cgroup.

    Why:
        SC-3: Kiln must detect that the kernel killed a worker for using too much memory, and
        back the suspicion with the cgroup's oom_kill counter (spec/04 §4.2).

    Args:
        args: parsed arguments.
    """
    # WHY OOMPolicy=continue: by default systemd *stops the whole unit* (sends SIGTERM to every
    # process in it, including the master) after the kernel OOM-kills any process inside it.
    # The first run of this lab hit exactly that; see the lab report.
    prefix = [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "-p",
        "MemoryMax=512M",
        "-p",
        "MemorySwapMax=0",
        "-p",
        "OOMPolicy=continue",
    ]
    server = Server(args.work / "oom", 2, {"KILN_LOG__LEVEL": "INFO"}, prefix=prefix)
    client = server.client()
    job = client.wait(client.submit("labs.alloc", kwargs={"mb": 700, "hold": True}), timeout_s=60)[
        "job"
    ]
    time.sleep(0.5)
    deaths = server.events("worker_died")
    alive = client.ping()["pong"]
    detected = server.events("cgroup_detected")
    code = server.stop()
    record = records.new_record(
        "K1", "oom", "alloc-700mb-in-512m", {"memory_max": "512M", "alloc_mb": 700}, 0
    )
    records.add_metric(
        record,
        "suspected_oom",
        "count",
        value=sum(1 for d in deaths if d["suspected_oom"] == "true"),
    )
    records.add_metric(
        record,
        "oom_confirmed",
        "count",
        value=sum(1 for d in deaths if d["oom_confirmed"] == "true"),
    )
    record["notes"] = json.dumps(
        {
            "job_state": job["state"],
            "job_error": job["error"],
            "deaths": deaths,
            "master_alive_after": alive,
            "master_exit": code,
            "cgroup": detected,
        }
    )  # type: ignore[index]
    save(record, args.results)
    print(f"  job: {job['state']} {job['error']}")  # type: ignore[index]
    for death in deaths:
        print(
            f"  worker_died how={death['how']} signal={death['signal']} "
            f"suspected_oom={death['suspected_oom']} oom_confirmed={death['oom_confirmed']} "
            f"evidence={death.get('oom_evidence')}"
        )
    print(f"  master still answering after the kill: {alive}; master exit code {code}")


def lab_reload(args: argparse.Namespace) -> None:
    """(b) 10,000 jobs with and without a reload every 2 s (SC-1, SC-2), plus a negative control.

    Why:
        The queue lives in the master (ADR-007), so reloads should drop nothing. The negative
        control (1 s grace, 3 s jobs) proves the measurement *can* see drops.

    Args:
        args: parsed arguments (--repeats).
    """
    for variant, every in (("no-reload", None), ("reload-every-2s", 2.0)):
        for repeat in range(args.repeats):
            server = Server(
                args.work / f"reload-{variant}-{repeat}", 4, {"KILN_LOG__LEVEL": "INFO"}
            )
            options = LoadOptions(
                jobs=10_000,
                concurrency=64,
                rate=None,
                task="labs.sleep",
                args=[],
                kwargs={"ms": 5},
                port=server.port,
                reload_every_s=every,
                pidfile=str(server.pidfile),
                experiment="reload",
                variant=variant,
                results_dir=args.results,
            )
            record, path = run_load(options)
            server.stop()
            print(f"  {variant} #{repeat}: {summary(record)} → {path.name}")
    server = Server(
        args.work / "reload-negative",
        2,
        {"KILN_LOG__LEVEL": "INFO", "KILN_MASTER__GRACEFUL_TIMEOUT_S": "1"},
    )
    options = LoadOptions(
        jobs=40,
        concurrency=8,
        rate=None,
        task="labs.sleep",
        args=[],
        kwargs={"ms": 3000},
        port=server.port,
        reload_every_s=2.0,
        pidfile=str(server.pidfile),
        experiment="reload",
        variant="negative-control-grace-1s",
        results_dir=args.results,
    )
    record, path = run_load(options)
    server.stop()
    print(f"  negative control: {summary(record)} → {path.name}")


def summary(record: dict[str, object]) -> str:
    """One-line summary of a load record.

    Why:
        The lab prints one line per run so progress and outliers are visible while it runs.

    Args:
        record: a record from run_load.

    Returns:
        The summary text.
    """
    metrics = record["metrics"]
    assert isinstance(metrics, dict)
    lat = metrics["latency"]["summary"]
    parts = [
        f"{name.removeprefix('jobs_')}={metrics[name]['value']}"
        for name in metrics
        if name.startswith("jobs_") and metrics[name]["value"]
    ]
    return (
        " ".join(parts)
        + f" reloads={metrics['reloads']['value']} tput={metrics['throughput']['value']}/s"
        + f" p50={lat['p50']}ms p99={lat['p99']}ms"
        + f" master_cpu={metrics.get('master_cpu', {}).get('value')}s"
    )


def lab_ceiling(args: argparse.Namespace) -> None:
    """(c) How many trivial jobs per second can one single-threaded master dispatch?

    Why:
        ADR-007's consequence: every job passes through the master, so the master's CPU is the
        ceiling. Varying workers and concurrency shows where throughput stops rising.

    Args:
        args: parsed arguments (--repeats).
    """
    for workers in (1, 2, 4, 8, 12):
        for concurrency in (16, 64, 256):
            for repeat in range(args.repeats):
                server = Server(
                    args.work / f"ceiling-{workers}-{concurrency}-{repeat}",
                    workers,
                    {"KILN_LOG__LEVEL": "INFO"},
                )
                options = LoadOptions(
                    jobs=args.jobs,
                    concurrency=concurrency,
                    rate=None,
                    task="labs.echo",
                    args=[],
                    kwargs={},
                    port=server.port,
                    pidfile=str(server.pidfile),
                    experiment="ceiling",
                    variant=f"w{workers}-c{concurrency}",
                    results_dir=args.results,
                )
                record, _ = run_load(options)
                server.stop()
                metrics = record["metrics"]
                assert isinstance(metrics, dict)
                cpu = metrics.get("master_cpu", {}).get("value")
                duration = float(str(record["duration_s"]))
                share = round(100 * cpu / duration, 1) if cpu is not None else None
                print(
                    f"  workers={workers} conc={concurrency} #{repeat}: "
                    f"{metrics['throughput']['value']} jobs/s, master CPU {share}%"
                )


def lab_heartbeat(args: argparse.Namespace) -> None:
    """(d) Does a busy worker stop heartbeating? Pure-Python CPU loop vs a GIL-holding C call.

    Why:
        The heartbeat thread can only run when the worker's main thread lets it. Python code
        lets it every few milliseconds; C code holding the GIL never does (spec/04 §5.4).

    Args:
        args: parsed arguments.
    """
    build = "freethreaded" if records.capture_interpreter()["build"] == "freethreaded" else "gil"
    for task, kwargs in (("labs.burn_cpu", {"ms": 15000}), ("labs.hold_gil", {"seconds": 20})):
        server = Server(
            args.work / f"heartbeat-{task}",
            1,
            {
                "KILN_LOG__LEVEL": "INFO",
                "KILN_WORKER__HEARTBEAT_INTERVAL_S": "1",
                "KILN_WORKER__HEARTBEAT_TIMEOUT_S": "10",
            },
        )
        client = server.client()
        job_id = client.submit(task, kwargs=kwargs, timeout_s=60)
        started = time.monotonic()
        max_gap = 0.0
        state = "RUNNING"
        while time.monotonic() - started < 40:
            stats = client.stats()
            for worker in stats["workers"]:  # type: ignore[union-attr]
                max_gap = max(max_gap, float(worker["last_heartbeat_age_s"]))  # type: ignore[index]
            state = str(client.status(job_id)["state"])
            if state != "RUNNING":
                break
            time.sleep(0.2)
        kills = server.events("worker_heartbeat_timeout")
        server.stop()
        record = records.new_record(
            "K1", "heartbeat", f"{task}-{build}", {"task": task, "kwargs": kwargs}, 0
        )
        records.add_metric(record, "max_heartbeat_age", "s", value=round(max_gap, 2))
        records.add_metric(record, "heartbeat_kills", "count", value=len(kills))
        record["notes"] = f"job state: {state}"
        save(record, args.results)
        print(
            f"  {build} {task}: max heartbeat age {max_gap:.2f}s, killed={bool(kills)}, job {state}"
        )


def lab_gilprobe(args: argparse.Namespace) -> None:
    """Gate G1.6: does ctypes.PyDLL(None).sleep() really hold the GIL (and CDLL release it)?

    Why:
        labs.hold_gil relies on it. A counter thread ticks every 10 ms; if it makes no progress
        during the C call, the call held the GIL.

    Args:
        args: parsed arguments.
    """
    counter = [0]
    stop = threading.Event()

    def tick() -> None:
        """Increment the counter every 10 ms until stopped."""
        while not stop.is_set():
            counter[0] += 1
            time.sleep(0.01)

    thread = threading.Thread(target=tick, daemon=True)
    thread.start()
    rows = {}
    for name, call in (
        ("cdll_sleep_2s", lambda: ctypes.CDLL(None).sleep(2)),
        ("pydll_sleep_2s", lambda: ctypes.PyDLL(None).sleep(2)),
        ("regex_n28", lambda: re.match(r"(a+)+$", "a" * 28 + "b")),
    ):
        before = counter[0]
        started = time.monotonic()
        call()
        rows[name] = {"ticks": counter[0] - before, "seconds": round(time.monotonic() - started, 2)}
    stop.set()
    record = records.new_record("K1", "gil-probe", records.capture_interpreter()["build"], {}, 0)  # type: ignore[arg-type]
    for name, row in rows.items():
        records.add_metric(record, f"{name}_ticks", "count", value=row["ticks"])
        records.add_metric(record, f"{name}_duration", "s", value=row["seconds"])
    save(record, args.results)
    for name, row in rows.items():
        print(f"  {name}: counter advanced {row['ticks']} ticks in {row['seconds']} s")


def main() -> None:
    """Parse the experiment name and run it.

    Why:
        One entry point per milestone keeps `dev.sh lab k1-<name>` simple.
    """
    parser = argparse.ArgumentParser(description="K1 labs (docs/labs/K1-supervisor.md)")
    parser.add_argument(
        "experiment", choices=("deaths", "oom", "reload", "ceiling", "heartbeat", "gilprobe")
    )
    parser.add_argument("--results", type=Path, default=REPO / "results")
    parser.add_argument("--work", type=Path, default=Path("/tmp/kiln-k1-lab"))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--jobs", type=int, default=5000)
    args = parser.parse_args()
    handlers = {
        "deaths": lab_deaths,
        "oom": lab_oom,
        "reload": lab_reload,
        "ceiling": lab_ceiling,
        "heartbeat": lab_heartbeat,
        "gilprobe": lab_gilprobe,
    }
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]  # WHY: show progress live
    print(f"== K1 lab: {args.experiment} (Python {sys.version.split()[0]})")
    handlers[args.experiment](args)


if __name__ == "__main__":
    main()
