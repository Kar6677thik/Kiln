"""K3 lab driver: copy-on-write decay per GC mode, composition, culprit detection, exit trick.

WHY THIS MODULE EXISTS
    A lab must be repeatable: the same commands and measurements, results saved as
    ExperimentRecords (ADR-022), so the numbers in docs/labs/K3-cow.md can be checked and
    re-run. This script starts real Kiln servers with a preloaded object graph and measures
    how much of it each worker still shares as it reads the data and as the GC runs.

HOW IT FITS
    ./scripts/dev.sh lab k3-cow          → cow          (a) sharing decay per gc_mode
    ./scripts/dev.sh lab k3-composition  → composition  (b) mortal vs immortal-heavy
    ./scripts/dev.sh lab k3-culprit      → culprit      (c) which gcwatch layer catches what
    ./scripts/dev.sh lab k3-exit         → exit         (d) the os._exit shutdown trick
    ./scripts/dev.sh lab k3-msgpack      → msgpack      gate G3.1
    Each writes results/K3/<experiment>/<run_id>.json; labs/k3/sampler.py records the
    200 ms time series from a separate process.

READ FIRST
    docs/labs/K3-cow.md, docs/spec/10-measurement-methodology.md,
    docs/concepts/copy-on-write.md, docs/concepts/smaps.md

MILESTONE
    Introduced in K3.
"""

import argparse
import gc
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

from kiln.bench import records
from kiln.client import KilnClient
from kiln.memory import memtable

REPO = Path(__file__).resolve().parents[2]
FIELD = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')
MIB = 1024 * 1024
WORKERS = 4
# The --workers option (default 4); a dict so Server can read what main() parsed.
WORKERS_ARG = {"n": WORKERS}
HOLD_MS = 300  # each lab task stays busy this long after its work (ADR-036)
MODES = ("default", "disabled", "threshold0", "freeze")
PHASES = (
    ("P0", None, {}),
    ("P1", "labs.touch_preloaded", {"fraction": 0.25}),
    ("P2", "labs.touch_preloaded", {"fraction": 1.0}),
    ("P3", "labs.churn", {"n": 200_000}),
    ("P4", "labs.gc_collect", {"generation": 2}),
)


def mem_available_mib() -> int:
    """MemAvailable from /proc/meminfo, in MiB.

    Why:
        The lab must not push WSL into the OOM killer (G3.9, §12 risk table).

    Returns:
        The available memory.
    """
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return 0


class Server:
    """A `kiln serve examples.k3_app:app` subprocess in its own process group.

    Why:
        Every run needs a fresh server (spec/10 §3) and must leave no process behind.
    """

    def __init__(self, workdir: Path, env: dict[str, str], workers: int = 0) -> None:
        """Start the server and wait until every worker is ready.

        Why:
            Measuring before all workers exist would mix "booting" with "sharing".

        Args:
            workdir: folder for the log.
            env: extra KILN_* settings.
            workers: worker count.

        Raises:
            RuntimeError: the server didn't become ready.
        """
        workdir.mkdir(parents=True, exist_ok=True)
        workers = workers or WORKERS_ARG["n"]
        self.workers = workers
        self.log_path = workdir / "kiln.log"
        self._log = self.log_path.open("w")
        command = [sys.executable, "-m", "kiln", "serve", "examples.k3_app:app", "--labs"]
        command += ["--workers", str(workers), "--bind", "127.0.0.1:0"]
        base = {"KILN_LOG__LEVEL": "INFO", "KILN_MASTER__BOOT_TIMEOUT_S": "180"}
        self.proc = subprocess.Popen(
            command,
            cwd=REPO,
            stderr=self._log,
            stdout=subprocess.DEVNULL,
            env={**os.environ, **base, **env},
            start_new_session=True,
        )
        self.port = 0
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            listening = self.events("listening")
            if listening and len(self.events("worker_ready")) >= workers:
                self.port = int(listening[0]["bind"].rsplit(":", 1)[1])
                return
            if self.proc.poll() is not None:
                break
            time.sleep(0.1)
        self.stop()
        raise RuntimeError(f"server not ready; see {self.log_path}")

    def events(self, name: str) -> list[dict[str, str]]:
        """Parse every log line with this event name.

        Why:
            The lab reads Kiln's own structured events (spec/06).

        Args:
            name: the event name.

        Returns:
            The parsed lines.
        """
        found = []
        for line in self.log_path.read_text(errors="replace").splitlines():
            if f"event={name} " in line + " ":
                found.append({k: v.strip('"') for k, v in FIELD.findall(line)})
        return found

    def worker_pids(self) -> set[int]:
        """The pids of the currently running workers (from worker_ready minus worker_died).

        Returns:
            The pids.
        """
        ready = {int(e["pid_worker"]) for e in self.events("worker_ready")}
        died = {int(e["pid_worker"]) for e in self.events("worker_died")}
        return ready - died

    def client(self) -> KilnClient:
        """A connected client with a long timeout (300 MiB collections take a while).

        Returns:
            The client.
        """
        client = KilnClient("127.0.0.1", self.port, timeout_s=600.0)
        client.connect()
        return client

    def stop(self) -> int:
        """SIGTERM the master, wait, then kill the whole group.

        Why:
            A stray worker holding a 300 MiB graph would distort the next run's memory.

        Returns:
            The master's exit code (-9 if it had to be killed).
        """
        try:
            os.kill(self.proc.pid, signal.SIGTERM)
            code = self.proc.wait(timeout=120)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            code = -9
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self._log.close()
        return code


def on_every_worker(server: Server, task: str, kwargs: dict[str, object]) -> dict[int, object]:
    """Run `task` until every worker has run it at least once (K3 §5.9 phase protocol).

    Why:
        The dispatcher picks workers itself. Submitting 2 × workers jobs at once almost
        always reaches all of them; we check the returned pids and retry up to 5 rounds.

    Args:
        server: the server.
        task: the lab task.
        kwargs: its arguments.

    Returns:
        pid → that worker's (last) result.

    Raises:
        RuntimeError: some worker never ran it.
    """
    wanted = server.worker_pids()
    results: dict[int, object] = {}
    client = server.client()
    for _ in range(5):
        # WHY: hold_ms keeps each worker busy briefly so the burst spreads over all of them.
        held = {**kwargs, "hold_ms": HOLD_MS} if task.startswith("labs.") else kwargs
        ids = [client.submit(task, kwargs=held) for _ in range(2 * len(wanted))]
        for job_id in ids:
            job = client.wait(job_id, timeout_s=300)["job"]
            assert isinstance(job, dict), job
            result = job.get("result")
            if job.get("state") != "SUCCEEDED" or not isinstance(result, dict):
                raise RuntimeError(f"{task} failed: {job}")
            results[int(result["pid"])] = result
        if wanted <= results.keys():
            return results
    raise RuntimeError(f"{task} reached {sorted(results)} but not all of {sorted(wanted)}")


def snapshot(server: Server) -> dict[str, object]:
    """Memory of every process plus each worker's GC counters, after a 1 s settle.

    Why:
        Pages are copied as the task runs; waiting a moment and reading from outside (this
        driver is a separate process) gives a stable, undisturbed reading.

    Args:
        server: the server.

    Returns:
        Per-worker lists, the master's numbers and Σ PSS.
    """
    time.sleep(1.0)
    rows = memtable.collect([], server.proc.pid)
    workers = [r for r in rows if r.role == "worker"]
    stats = on_every_worker(server, "labs.gc_stats", {})
    collections = [[s["collections"] for s in r["stats"]] for r in stats.values()]  # type: ignore[index]
    return {
        "shared": [w.rollup.shared for w in workers],
        "private_dirty": [w.rollup.private_dirty for w in workers],
        "pss": [w.rollup.pss for w in workers],
        "pss_anon": [w.rollup.pss_anon for w in workers],
        "uss": [w.rollup.uss for w in workers],
        "rss": [w.rollup.rss for w in workers],
        "sum_pss": sum(r.rollup.pss for r in rows),
        "sum_rss": sum(r.rollup.rss for r in rows),
        "parent_pss": sum(r.rollup.pss for r in rows if r.role in ("master", "forkserver")),
        "gen0": [c[0] for c in collections],
        "gen1": [c[1] for c in collections],
        "gen2": [c[2] for c in collections],
    }


def run_phases(
    server: Server, phases: tuple[tuple[str, str | None, dict[str, object]], ...]
) -> dict[str, dict[str, object]]:
    """Run each phase on every worker and snapshot after it.

    Why:
        The phases go from "nothing happened" to "read everything" to "the GC ran", so the
        snapshot differences show what each step copied.

    Args:
        server: the server.
        phases: (name, task or None, kwargs) in order.

    Returns:
        phase name → snapshot.
    """
    out = {}
    for name, task, kwargs in phases:
        if task is not None:
            on_every_worker(server, task, kwargs)
        out[name] = snapshot(server)
        if mem_available_mib() < 512:
            raise RuntimeError("MemAvailable below 512 MiB: aborting (K3 §12)")
    return out


def start_sampler(server: Server, out: Path) -> subprocess.Popen[bytes]:
    """Start labs/k3/sampler.py for this server.

    Why:
        spec/10 §7: memory is sampled from a separate process, which can't disturb it.

    Args:
        server: the server.
        out: the JSONL file.

    Returns:
        The sampler process.
    """
    return subprocess.Popen(
        [
            sys.executable,
            str(REPO / "labs" / "k3" / "sampler.py"),
            "--master",
            str(server.proc.pid),
            "--out",
            str(out),
        ],
        cwd=REPO,
    )


def save_phases(
    experiment: str,
    variant: str,
    params: dict[str, object],
    repeat: int,
    phases: dict[str, dict[str, object]],
    samples_file: Path | None,
    results: Path,
) -> None:
    """Write one ExperimentRecord with every phase's metrics (and the time series).

    Why:
        Every number in the lab report must come from a saved record (ADR-022).

    Args:
        experiment: the experiment name.
        variant: the variant name.
        params: the run's settings.
        repeat: the measured repeat index.
        phases: phase → snapshot.
        samples_file: the sampler's JSONL, or None.
        results: the results folder.
    """
    record = records.new_record("K3", experiment, variant, params, repeat)
    for phase, snap in phases.items():
        for key in ("shared", "private_dirty", "pss", "pss_anon", "uss", "rss"):
            records.add_metric(
                record, f"{phase}_{key}_per_worker_bytes", "bytes", samples=snap[key]
            )  # type: ignore[arg-type]
        for key in ("gen0", "gen1", "gen2"):
            records.add_metric(record, f"{phase}_{key}_collections", "count", samples=snap[key])  # type: ignore[arg-type]
        for key in ("sum_pss", "sum_rss", "parent_pss"):
            records.add_metric(record, f"{phase}_{key}_bytes", "bytes", value=snap[key])  # type: ignore[arg-type]
    if samples_file is not None and samples_file.exists():
        rows = [json.loads(line) for line in samples_file.read_text().splitlines() if line]
        workers = [r for r in rows if r["role"] == "worker"]
        records.add_metric(record, "ts_worker_t_s", "s", samples=[r["t_s"] for r in workers])
        records.add_metric(
            record, "ts_worker_shared_bytes", "bytes", samples=[r["shared"] for r in workers]
        )
        records.add_metric(
            record,
            "ts_worker_private_dirty_bytes",
            "bytes",
            samples=[r["private_dirty"] for r in workers],
        )
    records.write_record(record, results)


def one_run(
    args: argparse.Namespace,
    env: dict[str, str],
    tag: str,
    phases: tuple[tuple[str, str | None, dict[str, object]], ...],
) -> tuple[dict[str, dict[str, object]], Path, dict[str, str]]:
    """Start a server with `env`, sample it, run the phases, stop it.

    Why:
        One fresh server per run (spec/10 §3); `finally` guarantees the cleanup.

    Args:
        args: parsed arguments.
        env: KILN_* settings of this variant.
        tag: a unique folder name.
        phases: the phases to run.

    Returns:
        (phase snapshots, the samples file, the preload_graph_built event).
    """
    workdir = args.work / tag
    server = Server(workdir, env)
    samples = workdir / "samples.jsonl"
    sampler = start_sampler(server, samples)
    try:
        built = (server.events("preload_graph_built") or [{}])[0]
        result = run_phases(server, phases)
    finally:
        sampler.terminate()
        sampler.wait(timeout=10)
        server.stop()
    return result, samples, built


def lab_cow(args: argparse.Namespace) -> None:
    """(a) Sharing decay per gc_mode × start method (round-robin, fresh server per run).

    Why:
        The core K3 question: which GC strategy keeps the preloaded graph shared as the
        workers read it and as collections run (docs/concepts/copy-on-write.md)?

    Args:
        args: parsed arguments (--modes, --methods, --repeats, --mb).
    """
    need = args.mb * 2 * (args.workers + 1) + 512
    if mem_available_mib() < need:
        raise SystemExit(f"G3.9: need {need} MiB available, have {mem_available_mib()}")
    variants = [(mode, method) for method in args.methods for mode in args.modes]
    for repeat in range(args.repeats):
        for mode, method in variants:
            env = {
                "KILN_WORKER__GC_MODE": mode,
                "KILN_START__METHOD": method,
                "KILN_LABS__PRELOAD_MB": str(args.mb),
            }
            tag = f"cow-{mode}-{method}-{repeat}"
            phases, samples, built = one_run(args, env, tag, PHASES)
            params = {
                "mode": mode,
                "method": method,
                "mb": args.mb,
                "workers": args.workers,
                "graph": built,
            }
            save_phases("cow", f"{mode}-{method}", params, repeat, phases, samples, args.results)
            print(f"  {mode:10} {method:10} #{repeat}: " + summary_line(phases))


def summary_line(phases: dict[str, dict[str, object]]) -> str:
    """Median shared / private-dirty per worker for each phase, in MiB.

    Why:
        Printed per run so outliers are visible while the lab runs.

    Args:
        phases: phase → snapshot.

    Returns:
        The line.
    """
    parts = []
    for name, snap in phases.items():
        shared = statistics.median(snap["shared"]) / MIB  # type: ignore[arg-type]
        dirty = statistics.median(snap["private_dirty"]) / MIB  # type: ignore[arg-type]
        parts.append(f"{name} sh={shared:.0f} pd={dirty:.0f}")
    return " | ".join(parts)


def lab_composition(args: argparse.Namespace) -> None:
    """(b) mortal vs immortal-heavy leaf values, under default and freeze, phases P0–P2.

    Why:
        Immortal objects (PEP 683) never have their refcount written. If the leaves are
        immortal, only the containers' headers should be written by reading (gate G3.6).

    Args:
        args: parsed arguments.
    """
    for repeat in range(args.repeats):
        for composition in ("mortal", "immortal-heavy"):
            for mode in ("default", "freeze"):
                env = {
                    "KILN_WORKER__GC_MODE": mode,
                    "KILN_LABS__PRELOAD_MB": str(args.mb),
                    "KILN_LABS__PRELOAD_COMPOSITION": composition,
                }
                tag = f"comp-{composition}-{mode}-{repeat}"
                phases, _, built = one_run(args, env, tag, PHASES[:3])
                params = {"mode": mode, "composition": composition, "mb": args.mb, "graph": built}
                save_phases(
                    "composition",
                    f"{composition}-{mode}",
                    params,
                    repeat,
                    phases,
                    None,
                    args.results,
                )
                print(f"  {composition:15} {mode:8} #{repeat}: " + summary_line(phases))


def lab_culprit(args: argparse.Namespace) -> None:
    """(c) Which gcwatch layer catches which kind of GC re-enable, and what threshold0 changes.

    Why:
        Each layer sees a different kind of culprit (K3 §5.7); this checks the predictions of
        §5.8 against real logs. Under threshold0 the culprit's gc.enable() is harmless:
        churn must produce 0 automatic collections.

    Args:
        args: parsed arguments.
    """
    for culprit in ("python", "capi", "runtime"):
        for mode in ("disabled", "threshold0"):
            env = {
                "KILN_WORKER__GC_MODE": mode,
                "KILN_LABS__GC_WATCH": "true",
                "KILN_ROGUE_MODE": culprit,
                "KILN_WORKER__HEARTBEAT_INTERVAL_S": "0.5",
            }
            server = Server(args.work / f"culprit-{culprit}-{mode}", env, workers=2)
            try:
                before = on_every_worker(server, "labs.gc_stats", {})
                on_every_worker(server, "k3.parse", {"data": "[1]"})
                on_every_worker(server, "labs.churn", {"n": 200_000})
                time.sleep(1.5)  # WHY: give the heartbeat poll (layer C1) time to run
                after = on_every_worker(server, "labs.gc_stats", {})
                events = server.events("gc_reenabled")
            finally:
                server.stop()
            layers: dict[str, list[str]] = {}
            for event in events:
                layers.setdefault(event["layer"], []).append(
                    f"{event.get('role')}:{event.get('module')}"
                )
            automatic = {
                pid: sum(
                    a["collections"] - b["collections"]
                    for a, b in zip(after[pid]["stats"], before[pid]["stats"], strict=True)
                )  # type: ignore[index]
                for pid in after.keys() & before.keys()
            }
            record = records.new_record(
                "K3", "culprit", f"{culprit}-{mode}", {"culprit": culprit, "mode": mode}, 0
            )
            for layer in ("shim", "import_flip", "poll", "collection_while_disabled"):
                records.add_metric(
                    record, f"events_{layer}", "count", value=len(layers.get(layer, []))
                )
            records.add_metric(
                record, "collections_during_churn", "count", samples=list(automatic.values())
            )
            record["notes"] = json.dumps({"layers": layers, "events": events[:20]})
            records.write_record(record, args.results)
            counts = {k: len(v) for k, v in layers.items()}
            print(
                f"  {culprit:8} {mode:10}: layers={counts} "
                f"collections_during_churn={sorted(automatic.values())}"
            )


def lab_exit(args: argparse.Namespace) -> None:
    """(d) Shutdown time and exit handlers of spawned workers, with and without the trick.

    Why:
        Instagram's atexit.register(os._exit, 0) skipped the final collection and module
        teardown. Here each spawned worker holds its own graph, so finalization has real
        work to skip (docs/concepts/finalization-and-atexit.md).

    Args:
        args: parsed arguments.
    """
    for repeat in range(args.repeats):
        for trick in (False, True):
            marks = args.work / f"exit-marks-{trick}-{repeat}"
            marks.mkdir(parents=True, exist_ok=True)
            env = {
                "KILN_START__METHOD": "spawn",
                "KILN_LABS__PRELOAD_MB": str(args.mb),
                "KILN_WORKER__EXIT_VIA_OS_EXIT": "true" if trick else "false",
                "KILN_K3_MARK_DIR": str(marks),
            }
            server = Server(args.work / f"exit-{trick}-{repeat}", env, workers=1)
            pid = next(iter(server.worker_pids()))
            server.stop()
            finished = server.events("shutdown_finished")
            duration = float(finished[0]["duration_ms"]) if finished else float("nan")
            ran_atexit = (marks / f"atexit-{pid}").exists()
            ran_del = (marks / f"final_gc-{pid}").exists()
            record = records.new_record(
                "K3", "exit", f"spawn-trick-{trick}", {"trick": trick, "mb": args.mb}, repeat
            )
            records.add_metric(record, "shutdown_ms", "ms", value=duration)
            records.add_metric(record, "atexit_ran", "count", value=int(ran_atexit))
            records.add_metric(record, "final_collection_ran", "count", value=int(ran_del))
            records.write_record(record, args.results)
            print(
                f"  trick={trick!s:5} #{repeat}: shutdown {duration} ms, "
                f"atexit={ran_atexit}, final_gc={ran_del}"
            )


def lab_msgpack(args: argparse.Namespace) -> None:
    """Gate G3.1: does today's msgpack re-enable the GC (at import or in unpackb)?

    Why:
        Errata E-03: the research quotes Instagram's 2017 finding; the library may have
        changed since. Requires the lab-k3 dependency group.

    Args:
        args: parsed arguments.
    """
    gc.disable()
    import msgpack  # WHY: imported after gc.disable() on purpose

    after_import = gc.isenabled()
    msgpack.unpackb(msgpack.packb([{}] * 10_000))
    after_unpack = gc.isenabled()
    gc.enable()
    record = records.new_record(
        "K3", "msgpack-gate", "G3.1", {"msgpack": ".".join(map(str, msgpack.version))}, 0
    )
    records.add_metric(record, "enabled_after_import", "count", value=int(after_import))
    records.add_metric(record, "enabled_after_unpackb", "count", value=int(after_unpack))
    records.write_record(record, args.results)
    print(
        f"  msgpack {msgpack.version}: enabled after import={after_import}, "
        f"after unpackb={after_unpack}"
    )


def main() -> None:
    """Parse the experiment name and run it.

    Why:
        One entry point per milestone keeps `dev.sh lab k3-<name>` simple.
    """
    parser = argparse.ArgumentParser(description="K3 labs (docs/labs/K3-cow.md)")
    parser.add_argument("experiment", choices=("cow", "composition", "culprit", "exit", "msgpack"))
    parser.add_argument("--results", type=Path, default=REPO / "results")
    parser.add_argument("--work", type=Path, default=Path("/tmp/kiln-k3-lab"))
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--mb", type=int, default=300)
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument("--modes", nargs="+", default=list(MODES), choices=MODES)
    parser.add_argument(
        "--methods", nargs="+", default=["fork"], choices=("fork", "spawn", "forkserver")
    )
    args = parser.parse_args()
    WORKERS_ARG["n"] = args.workers
    handlers = {
        "cow": lab_cow,
        "composition": lab_composition,
        "culprit": lab_culprit,
        "exit": lab_exit,
        "msgpack": lab_msgpack,
    }
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]  # WHY: show progress live
    print(f"== K3 lab: {args.experiment} (Python {sys.version.split()[0]})")
    handlers[args.experiment](args)


if __name__ == "__main__":
    main()
