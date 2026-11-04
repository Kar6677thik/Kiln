"""K4 lab (a): throughput of each executor for CPU-bound and IO-bound jobs, GIL vs free-threaded.

WHY THIS MODULE EXISTS
    "Should I use threads, subinterpreters, asyncio or processes?" depends on whether the
    work computes or waits, and on whether the GIL is there. This driver runs the same load
    against one Kiln worker with C slots under each executor (and against C sync worker
    processes, Kiln's K1 model), and records throughput, latency, CPU use and memory.
    Run it once per interpreter: KILN_PY=3.15 and KILN_PY=3.15t.

HOW IT FITS
    ./scripts/dev.sh lab k4-bench [--repeats 5] [--jobs 400]
        for each cell: kiln serve examples.demo:app --labs (from the ext4 copy)
            ──kiln.bench.load (closed loop, concurrency 64)──▶ throughput, p50/p99
            /proc/<worker>/stat before/after ──▶ CPU utilization (1.0 = one core)
            stats ──▶ gil_enabled (a 3.15t run with the GIL on is rejected)
        one ExperimentRecord per run in results/K4/matrix/

READ FIRST
    docs/labs/K4-concurrency.md, docs/concepts/cpu-vs-io-bound.md, docs/concepts/gil.md,
    docs/concepts/amdahls-law.md

MILESTONE
    Introduced in K4.
"""

import argparse
import os
import random
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

from kiln.bench import records
from kiln.bench.load import LoadOptions, run_load
from kiln.client import KilnClient
from kiln.memory.procfs import ProcessGone, read_cpu_times, read_smaps_rollup

REPO = Path(__file__).resolve().parents[2]
SNAPSHOT = Path.home() / "kiln-bench"
WORKLOADS = {
    # WHY: fixed work (≈ 20 ms on 3.15), not fixed time: see labs.burn_cpu's GOTCHA.
    "cpu": ("labs.burn_cpu", {"work": 1100}),
    "io": ("labs.sleep", {"ms": 20}),
    "aio": ("labs.async_sleep", {"ms": 20}),
}
EXECUTORS = ("sync", "threads", "interpreters", "asyncio")
CONCURRENCY = (1, 2, 4, 8)
FREE_THREADED = not getattr(sys, "_is_gil_enabled", lambda: True)()


def make_snapshot() -> Path:
    """Copy the code onto ext4 (~/kiln-bench), as lab K2 did (ADR-003, ADR-035 §6).

    Why:
        Timings must not include the slow 9P filesystem.

    Returns:
        The snapshot root.
    """
    for folder in ("src", "examples", "labs"):
        target = SNAPSHOT / folder
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(REPO / folder, target, ignore=shutil.ignore_patterns("__pycache__"))
    return SNAPSHOT


def free_port() -> int:
    """Ask the kernel for an unused TCP port.

    Why:
        Logs run at WARNING to keep logging cost out of the measurement, so the
        `listening` event (INFO) isn't available to discover the port.

    Returns:
        A port number.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def cells(include_interpreters: bool) -> list[tuple[str, str, int]]:
    """Every (workload, executor, concurrency) cell of the matrix.

    Why:
        async_sleep only makes sense under asyncio (it's the native async path); the
        interpreters executor needs 3.14+.

    Args:
        include_interpreters: whether this Python supports the interpreters executor.

    Returns:
        The cells.
    """
    out = []
    for workload in ("cpu", "io"):
        for executor in EXECUTORS:
            if executor == "interpreters" and not include_interpreters:
                continue
            out.extend((workload, executor, c) for c in CONCURRENCY)
    out.extend(("aio", "asyncio", c) for c in CONCURRENCY)
    return out


def one_run(args: argparse.Namespace, root: Path, cell: tuple[str, str, int]) -> dict[str, object]:
    """Start a server for the cell, drive the load, measure, stop.

    Why:
        A fresh server per run (spec/10 §3): no warm caches carried between cells.

    Args:
        args: parsed arguments.
        root: where the code lives (the ext4 snapshot).
        cell: (workload, executor, concurrency).

    Returns:
        The measurements.
    """
    workload, executor, concurrency = cell
    # WHY: the process-parallel reference row is K1's model: C sync worker processes.
    workers = concurrency if executor == "sync" else 1
    port = free_port()
    env = {
        **os.environ,
        "PYTHONPATH": str(root / "src"),
        "KILN_LOG__LEVEL": "WARNING",
        "KILN_WORKER__EXECUTOR_CONCURRENCY": str(concurrency),
        "KILN_WORKER__HEARTBEAT_INTERVAL_S": "0.5",
    }
    command = [sys.executable, "-m", "kiln", "serve", "examples.demo:app", "--labs"]
    command += ["--workers", str(workers), "--executor", executor, "--bind", f"127.0.0.1:{port}"]
    proc = subprocess.Popen(
        command,
        cwd=root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        client = wait_ready(port, workers)
        pids = [int(str(w["pid"])) for w in client.stats()["workers"]]  # type: ignore[union-attr]
        cpu_before = sum(read_cpu_times(pid) for pid in pids)
        task, kwargs = WORKLOADS[workload]
        options = LoadOptions(
            jobs=args.jobs,
            concurrency=64,
            rate=None,
            task=task,
            args=[],
            kwargs=kwargs,
            port=port,
            experiment="k4-load",
            variant=f"{workload}-{executor}-c{concurrency}",
            results_dir=args.work / "load-records",
        )
        record, _ = run_load(options)
        duration = float(str(record["duration_s"]))
        cpu_after = sum(read_cpu_times(pid) for pid in pids)
        rss = sum(read_smaps_rollup(pid).rss for pid in pids)
        stats_workers = client.stats()["workers"]
        assert isinstance(stats_workers, list)
        gil = any(bool(w.get("gil_enabled")) for w in stats_workers)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=30)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    metrics = record["metrics"]
    assert isinstance(metrics, dict)
    summary = metrics["latency"]["summary"]
    return {
        "throughput": metrics["throughput"]["value"],
        "succeeded": metrics["jobs_succeeded"]["value"],
        "p50": summary["p50"],
        "p99": summary["p99"],
        "cpu_util": round((cpu_after - cpu_before) / duration, 3),
        "rss_bytes": rss,
        "gil_enabled": gil,
        "workers": workers,
    }


def wait_ready(port: int, workers: int) -> KilnClient:
    """Connect and wait until `workers` workers are READY.

    Why:
        Load must not start while workers are still booting (that would measure boot).

    Args:
        port: the server's port.
        workers: how many READY workers to wait for.

    Returns:
        A connected client.

    Raises:
        RuntimeError: not ready within 60 s.
    """
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            client = KilnClient("127.0.0.1", port, timeout_s=60)
            client.connect()
            listed = client.stats()["workers"]
            assert isinstance(listed, list)
            if sum(1 for w in listed if w["state"] == "READY") >= workers:
                return client
            client.close()
        except (OSError, ProcessGone):
            pass
        time.sleep(0.1)
    raise RuntimeError(f"server on port {port} not ready")


def main() -> None:
    """Run every cell round-robin (seeded shuffle per round), warm-ups first.

    Why:
        spec/10 §3: interleaving spreads slow drift (thermal, background work) over every
        variant instead of penalising whichever ran last.
    """
    parser = argparse.ArgumentParser(description="K4 executor benchmark")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--jobs", type=int, default=400)
    parser.add_argument("--seed", type=int, default=20261104)
    parser.add_argument("--results", type=Path, default=REPO / "results")
    parser.add_argument("--work", type=Path, default=Path("/tmp/kiln-k4-bench"))
    parser.add_argument("--only", nargs="*", default=None, help="limit to workloads (cpu io aio)")
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]  # WHY: live progress
    tag = "3.15t" if FREE_THREADED else f"{sys.version_info[0]}.{sys.version_info[1]}"
    print(
        f"== K4 bench on {sys.version.split()[0]} ({'free-threaded' if FREE_THREADED else 'GIL'})"
    )
    root = make_snapshot()
    all_cells = [
        c for c in cells(sys.version_info >= (3, 14)) if not args.only or c[0] in args.only
    ]
    rng = random.Random(args.seed)
    results: dict[tuple[str, str, int], list[dict[str, object]]] = {c: [] for c in all_cells}
    for round_index in range(args.warmups + args.repeats):
        order = all_cells[:]
        rng.shuffle(order)
        measured = round_index >= args.warmups
        for cell in order:
            run = one_run(args, root, cell)
            if FREE_THREADED and run["gil_enabled"]:
                print(f"  REJECTED {cell}: the GIL was enabled on a free-threaded build")
                continue
            if not measured:
                continue
            results[cell].append(run)
            workload, executor, concurrency = cell
            params = {
                "workload": workload,
                "executor": executor,
                "concurrency": concurrency,
                "workers": run["workers"],
                "jobs": args.jobs,
                "interp": tag,
            }
            record = records.new_record(
                "K4",
                "matrix",
                f"{workload}-{executor}-c{concurrency}",
                params,
                round_index - args.warmups,
            )
            record["host"]["notes"] = "source=ext4-snapshot"  # type: ignore[index]
            records.add_metric(record, "throughput", "jobs/s", value=float(str(run["throughput"])))
            records.add_metric(record, "latency_p50", "ms", value=float(str(run["p50"])))
            records.add_metric(record, "latency_p99", "ms", value=float(str(run["p99"])))
            records.add_metric(record, "cpu_util", "ratio", value=float(str(run["cpu_util"])))
            records.add_metric(
                record, "worker_rss_bytes", "bytes", value=int(str(run["rss_bytes"]))
            )
            records.add_metric(record, "gil_enabled", "count", value=int(bool(run["gil_enabled"])))
            records.add_metric(record, "jobs_succeeded", "count", value=int(str(run["succeeded"])))
            records.write_record(record, args.results)
        print(f"  round {round_index + 1}/{args.warmups + args.repeats} done")
    for (workload, executor, concurrency), runs in results.items():
        if not runs:
            continue
        tput = statistics.median(float(str(r["throughput"])) for r in runs)
        cpu = statistics.median(float(str(r["cpu_util"])) for r in runs)
        print(f"  {workload:3} {executor:12} C={concurrency}: {tput:8.1f} jobs/s  cpu {cpu:.2f}")


if __name__ == "__main__":
    main()
