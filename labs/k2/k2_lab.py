"""K2 lab driver: what each start method costs, and how well forkcheck is calibrated.

WHY THIS MODULE EXISTS
    A lab must be repeatable: same commands, same measurements, results saved as
    ExperimentRecords (ADR-022), so the numbers in docs/labs/K2-forkcheck.md can be checked and
    re-run. This script starts real Kiln servers with each start method and measures them.

HOW IT FITS
    ./scripts/dev.sh lab k2-startup   → startup    (a) launch→ready time and memory per method
    ./scripts/dev.sh lab k2-calibrate → calibrate  (b) forkcheck precision/recall + --confirm
    Each writes results/K2/<experiment>/<run_id>.json and prints a summary table.

READ FIRST
    docs/labs/K2-forkcheck.md, docs/spec/10-measurement-methodology.md,
    docs/concepts/start-methods.md, docs/concepts/memory-metrics.md

MILESTONE
    Introduced in K2.
"""

import argparse
import os
import random
import re
import shutil
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

# PY: confirm_harness.py sits next to this file; Python puts a script's own folder on
# sys.path, so it imports like a module.
from confirm_harness import run_calibration

from kiln.bench import records

REPO = Path(__file__).resolve().parents[2]
SNAPSHOT = Path.home() / "kiln-bench"
FIELD = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')
METHODS = ("fork", "forkserver", "spawn")
APPS = {"light": "examples.demo:app", "heavy": "heavy_app:app"}
WORKERS = 4


def make_snapshot() -> Path:
    """Copy the code the servers run onto WSL's ext4 disk (~/kiln-bench).

    Why:
        ADR-003: startup timings measured from /mnt/c mostly measure the slow 9P filesystem.
        scripts/bench-snapshot.sh needs rsync, which this machine doesn't have yet (it needs
        `sudo apt install rsync`), so the lab copies the three folders it needs itself.

    Returns:
        The snapshot root.
    """
    for folder in ("src", "examples", "labs"):
        target = SNAPSHOT / folder
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(REPO / folder, target, ignore=shutil.ignore_patterns("__pycache__"))
    return SNAPSHOT


def smaps_rollup(pid: int) -> dict[str, int]:
    """Read Rss, Pss and USS (private clean + private dirty) of a process, in bytes.

    Why:
        RSS counts shared pages fully in every process, so it over-counts; PSS divides each
        shared page between its sharers, so PSS values add up to the real total
        (docs/concepts/memory-metrics.md). K3 adds a proper reader in kiln.memory; the K2 spec
        allows this minimal one.

    Args:
        pid: the process.

    Returns:
        {"rss": ..., "pss": ..., "uss": ...} in bytes.
    """
    values: dict[str, int] = {}
    for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] == "kB":
            values[parts[0].rstrip(":")] = int(parts[1]) * 1024
    return {
        "rss": values["Rss"],
        "pss": values["Pss"],
        "uss": values["Private_Clean"] + values["Private_Dirty"],
    }


class Server:
    """A `kiln serve` subprocess in its own process group, run from a chosen folder.

    Why:
        Every run needs a fresh server (spec/10 §3: fresh process for startup and memory
        experiments) and must never leave stray processes behind.
    """

    def __init__(self, workdir: Path, root: Path, method: str, app: str) -> None:
        """Start the server; don't wait for anything yet.

        Why:
            Starting is separate from waiting so the caller decides how long to wait and
            what to measure; the server runs from `root`, so ext4 vs 9P is just a folder.

        Args:
            workdir: folder for the log.
            root: where the code lives (the repo on 9P, or the ext4 snapshot).
            method: start.method.
            app: "light" or "heavy".
        """
        workdir.mkdir(parents=True, exist_ok=True)
        self.log_path = workdir / "kiln.log"
        env = {
            **os.environ,
            "KILN_START__METHOD": method,
            "KILN_LOG__LEVEL": "INFO",
            "KILN_APP__PYTHONPATH": f'["{root / "labs" / "k2"}"]',
            # WHY: put the snapshot's own src/ first, so `import kiln` reads ext4, not /mnt/c.
            "PYTHONPATH": str(root / "src"),
        }
        command = [sys.executable, "-m", "kiln", "serve", APPS[app]]
        command += ["--labs", "--workers", str(WORKERS), "--bind", "127.0.0.1:0"]
        self._log = self.log_path.open("w")
        self.proc = subprocess.Popen(
            command,
            cwd=root,
            stderr=self._log,
            stdout=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )

    def events(self, name: str) -> list[dict[str, str]]:
        """Parse every log line with this event name.

        Why:
            The lab reads Kiln's own structured events (spec/06), so the timings are the
            master's own monotonic-clock measurements, not the lab's guesses.

        Args:
            name: the event name.

        Returns:
            The parsed lines.
        """
        found = []
        for line in self.log_path.read_text(errors="replace").splitlines():
            if f"event={name} " in line + " ":
                found.append({key: value.strip('"') for key, value in FIELD.findall(line)})
        return found

    def wait_ready(self, timeout_s: float = 60.0) -> list[dict[str, str]]:
        """Wait until all workers reported ready.

        Why:
            Measuring before every worker is ready would read half-started processes.

        Args:
            timeout_s: give up after this long.

        Returns:
            The worker_ready events.

        Raises:
            RuntimeError: the workers didn't become ready in time.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            ready = self.events("worker_ready")
            if len(ready) >= WORKERS:
                return ready[:WORKERS]
            if self.proc.poll() is not None:
                break
            time.sleep(0.05)
        raise RuntimeError(f"workers not ready; see {self.log_path}")

    def stop(self) -> None:
        """SIGTERM the master, then make sure the whole group is gone.

        Why:
            A stray worker would add its memory and CPU to the next run.
        """
        try:
            os.kill(self.proc.pid, signal.SIGTERM)
            self.proc.wait(timeout=30)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self._log.close()


def one_run(
    args: argparse.Namespace, fs: str, root: Path, method: str, app: str, tag: str
) -> dict[str, object]:
    """Start one server, wait for its workers, measure, stop.

    Why:
        One run = one fresh server (spec/10 §3), and `finally` guarantees it is stopped
        even if a measurement fails.

    Args:
        args: parsed arguments.
        fs: "ext4" or "9p".
        root: where the code lives.
        method: start.method.
        app: "light" or "heavy".
        tag: a unique run folder name.

    Returns:
        The measurements of this run.
    """
    server = Server(args.work / tag, root, method, app)
    try:
        ready = server.wait_ready()
        time.sleep(args.settle_s)  # WHY: let the workers finish touching memory after `ready`
        launcher = server.events("launcher_started")[0]
        workers = [smaps_rollup(int(event["pid_worker"])) for event in ready]
        master = smaps_rollup(server.proc.pid)
        helper_pid = int(launcher["pid_launcher"])
        helper = smaps_rollup(helper_pid) if helper_pid else {"rss": 0, "pss": 0, "uss": 0}
    finally:
        server.stop()
    return {
        "boot_ms": [float(event["boot_ms"]) for event in ready],
        "launcher_start_ms": float(launcher["start_ms"]),
        "pss": [w["pss"] for w in workers],
        "uss": [w["uss"] for w in workers],
        "rss": [w["rss"] for w in workers],
        "master_pss": master["pss"],
        "forkserver_pss": helper["pss"],
        "total_pss": master["pss"] + helper["pss"] + sum(w["pss"] for w in workers),
    }


def save_run(
    fs: str, method: str, app: str, repeat: int, run: dict[str, object], results: Path
) -> None:
    """Write one run as an ExperimentRecord.

    Why:
        Every number in the lab report must come from a saved record (ADR-022).

    Args:
        fs: "ext4" or "9p".
        method: start.method.
        app: "light" or "heavy".
        repeat: the measured repeat index.
        run: the measurements from one_run().
        results: the results folder.
    """
    params: dict[str, object] = {"method": method, "app": APPS[app], "fs": fs, "workers": WORKERS}
    record = records.new_record("K2", "startup", f"{method}-{app}-{fs}", params, repeat)
    host = record["host"]
    assert isinstance(host, dict)
    # WHY: the lab process may run from /mnt/c while the *server* runs from the snapshot;
    # record where the measured code actually lived.
    host["notes"] = "source=ext4-snapshot" if fs == "ext4" else "source=9p"
    for name in ("boot_ms",):
        records.add_metric(record, "launch_to_ready_ms", "ms", samples=run[name])  # type: ignore[arg-type]
    records.add_metric(record, "launcher_start_ms", "ms", value=run["launcher_start_ms"])  # type: ignore[arg-type]
    records.add_metric(record, "pss_per_worker_bytes", "bytes", samples=run["pss"])  # type: ignore[arg-type]
    records.add_metric(record, "uss_per_worker_bytes", "bytes", samples=run["uss"])  # type: ignore[arg-type]
    records.add_metric(record, "rss_per_worker_bytes", "bytes", samples=run["rss"])  # type: ignore[arg-type]
    for name in ("master_pss", "forkserver_pss", "total_pss"):
        records.add_metric(record, f"{name}_bytes", "bytes", value=run[name])  # type: ignore[arg-type]
    records.write_record(record, results)


def bootstrap_ci(values: list[float], seed: int = 2) -> tuple[float, float]:
    """A 95% bootstrap confidence interval for the median (spec/10 §4.2).

    Why:
        Startup times are skewed (a few slow runs), so the median plus a resampled interval is
        more honest than mean ± stdev.

    Args:
        values: one number per measured run.
        seed: fixed, so the printed interval is reproducible.

    Returns:
        (low, high).
    """
    rng = random.Random(seed)
    medians = sorted(statistics.median(rng.choices(values, k=len(values))) for _ in range(2000))
    return medians[int(0.025 * len(medians))], medians[int(0.975 * len(medians)) - 1]


def lab_startup(args: argparse.Namespace) -> None:
    """(a) launch→ready time and memory per worker for fork / forkserver / spawn.

    Why:
        The start method decides what a worker copies (everything, a clean preload, or
        nothing) and so what it costs in time and memory (docs/concepts/start-methods.md).
        Variants run round-robin with a seeded shuffle inside each round (spec/10 §3).

    Args:
        args: parsed arguments (--repeats, --warmups, --fs).
    """
    roots = {"ext4": make_snapshot(), "9p": REPO}
    variants = [(fs, app, method) for fs in args.fs for app in APPS for method in METHODS]
    rng = random.Random(args.seed)
    print(
        f"  {len(variants)} variants × ({args.warmups} warm-up + {args.repeats} measured),"
        f" seed {args.seed}"
    )
    per_variant: dict[tuple[str, str, str], list[dict[str, object]]] = {v: [] for v in variants}
    for round_index in range(args.warmups + args.repeats):
        order = variants[:]
        rng.shuffle(order)
        measured = round_index >= args.warmups
        for fs, app, method in order:
            tag = f"{fs}-{app}-{method}-{round_index}"
            run = one_run(args, fs, roots[fs], method, app, tag)
            if measured:
                repeat = round_index - args.warmups
                save_run(fs, method, app, repeat, run, args.results)
                per_variant[(fs, app, method)].append(run)
        label = "measured" if measured else "warm-up"
        print(f"  round {round_index + 1}/{args.warmups + args.repeats} ({label}) done")
    print_startup_table(per_variant)


def print_startup_table(per_variant: dict[tuple[str, str, str], list[dict[str, object]]]) -> None:
    """Print the lab report's table (a).

    Why:
        Printing the exact table shape of the lab report makes the numbers easy to check.

    Args:
        per_variant: the measured runs per (fs, app, method).
    """
    mib = 1024 * 1024
    print(
        "\n  method | app | fs | launch→ready ms median [95% CI] | launcher start ms | "
        "PSS/worker MiB | USS/worker MiB | RSS/worker MiB | total PSS MiB"
    )
    for (fs, app, method), runs in per_variant.items():
        boots = [statistics.median(r["boot_ms"]) for r in runs]  # type: ignore[arg-type]
        low, high = bootstrap_ci(boots)
        start = statistics.median([float(r["launcher_start_ms"]) for r in runs])  # type: ignore[arg-type]
        pss = statistics.median([statistics.median(r["pss"]) for r in runs]) / mib  # type: ignore[arg-type]
        uss = statistics.median([statistics.median(r["uss"]) for r in runs]) / mib  # type: ignore[arg-type]
        rss = statistics.median([statistics.median(r["rss"]) for r in runs]) / mib  # type: ignore[arg-type]
        total = statistics.median([float(r["total_pss"]) for r in runs]) / mib  # type: ignore[arg-type]
        print(
            f"  {method} | {app} | {fs} | {statistics.median(boots):.1f} [{low:.1f}, {high:.1f}] | "
            f"{start:.1f} | {pss:.1f} | {uss:.1f} | {rss:.1f} | {total:.1f}"
        )


def lab_calibrate(args: argparse.Namespace) -> None:
    """(b) forkcheck precision and recall per rule, and --confirm on the real cases.

    Why:
        A checker's warnings are only useful if they are usually right (precision) and it
        rarely misses real problems (recall); docs/concepts/static-vs-dynamic-analysis.md.

    Args:
        args: parsed arguments.
    """
    run_calibration(REPO, args.results)


def main() -> None:
    """Parse the experiment name and run it.

    Why:
        One entry point per milestone keeps `dev.sh lab k2-<name>` simple.
    """
    parser = argparse.ArgumentParser(description="K2 labs (docs/labs/K2-forkcheck.md)")
    parser.add_argument("experiment", choices=("startup", "calibrate"))
    parser.add_argument("--results", type=Path, default=REPO / "results")
    parser.add_argument("--work", type=Path, default=Path("/tmp/kiln-k2-lab"))
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--settle-s", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=20261104)
    parser.add_argument("--fs", nargs="+", default=["ext4", "9p"], choices=("ext4", "9p"))
    args = parser.parse_args()
    handlers = {"startup": lab_startup, "calibrate": lab_calibrate}
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]  # WHY: show progress live
    print(f"== K2 lab: {args.experiment} (Python {sys.version.split()[0]})")
    handlers[args.experiment](args)


if __name__ == "__main__":
    main()
