"""A planted data race (RacyStats) and its fix (LockedStats), measured over many trials.

WHY THIS MODULE EXISTS
    LAB: RacyStats is deliberately wrong. `self.count += 1` reads, adds and writes in three
    separate steps; two threads can both read 41, both write 42, and one update is lost.
    On the GIL build that needs a thread switch at exactly the wrong moment, so it is rare;
    on the free-threaded build (3.15t) threads really run at the same time, so it's common.
    This lab measures both, and shows the one-line fix (docs/concepts/data-races.md).

HOW IT FITS
    ./scripts/dev.sh lab k4-race [--impl racy|locked] [--threads 8] [--iters 200000]
        T threads wait on a Barrier ──▶ all call record("t") N times ──▶ count vs T × N
        one ExperimentRecord per trial in results/K4/race/
    Kiln's own worker counters use the LockedStats pattern (worker/main.py, WorkerState.lock).

READ FIRST
    docs/concepts/data-races.md, docs/concepts/thread-safety-of-builtins.md,
    docs/python/threading.md, docs/python/locks.md

MILESTONE
    Introduced in K4.
"""

import argparse
import sys
import threading
import time
from pathlib import Path

from kiln.bench import records

REPO = Path(__file__).resolve().parents[2]


class RacyStats:
    """LAB: deliberately racy. Never copy this pattern.

    Why:
        It is the shape of countless real counters: correct with one thread, silently
        wrong with several.
    """

    def __init__(self) -> None:
        """Start at zero."""
        self.count = 0
        self.per_task: dict[str, int] = {}

    def record(self, task: str) -> None:
        """Count one finished job of `task`.

        Args:
            task: the task name.
        """
        self.count += 1  # read, add, write: three steps
        # On 3.15t each dict operation is internally locked, so the dict is never corrupted;
        # but get-then-set is TWO operations, so updates can still be lost between them.
        self.per_task[task] = self.per_task.get(task, 0) + 1


class LockedStats(RacyStats):
    """The fix: one lock makes each record() a single, indivisible step.

    Why:
        A lock lets only one thread at a time run the read-add-write sequence.
    """

    def __init__(self) -> None:
        """Start at zero, with a lock."""
        super().__init__()
        self._lock = threading.Lock()

    def record(self, task: str) -> None:
        """Count one finished job of `task`, under the lock.

        Args:
            task: the task name.
        """
        with self._lock:
            super().record(task)


def trial(impl: str, threads: int, iters: int) -> tuple[int, int, float]:
    """Run one trial: `threads` threads each record() `iters` times, all starting together.

    Why:
        A Barrier releases every thread at the same instant, which makes the overlap (and
        so the race) as likely as possible and the experiment repeatable.

    Args:
        impl: "racy" or "locked".
        threads: how many threads.
        iters: record() calls per thread.

    Returns:
        (lost count updates, lost per-task updates, seconds).
    """
    stats = LockedStats() if impl == "locked" else RacyStats()
    barrier = threading.Barrier(threads)

    def work() -> None:
        """One thread's share: wait for the others, then record() `iters` times."""
        barrier.wait()
        for _ in range(iters):
            stats.record("t")

    pool = [threading.Thread(target=work) for _ in range(threads)]
    started = time.perf_counter()
    for thread in pool:
        thread.start()
    for thread in pool:
        thread.join()
    elapsed = time.perf_counter() - started
    expected = threads * iters
    return expected - stats.count, expected - stats.per_task.get("t", 0), elapsed


def main() -> None:
    """Run the trials and write one record each.

    Why:
        Races are timing-dependent; only many trials show how often they bite.
    """
    parser = argparse.ArgumentParser(description="K4 race demo (docs/labs/K4-concurrency.md)")
    parser.add_argument("--impl", choices=("racy", "locked"), default="racy")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--iters", type=int, default=200_000)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--results", type=Path, default=REPO / "results")
    parser.add_argument("--no-records", action="store_true", help="print only (tests)")
    args = parser.parse_args()
    gil = getattr(sys, "_is_gil_enabled", lambda: True)()
    print(f"== race demo: impl={args.impl} threads={args.threads} iters={args.iters} gil={gil}")
    lost_runs = 0
    for repeat in range(args.repeats):
        lost, lost_dict, elapsed = trial(args.impl, args.threads, args.iters)
        lost_runs += lost > 0 or lost_dict > 0
        print(
            f"  #{repeat}: lost count updates={lost} "
            f"lost dict updates={lost_dict} ({elapsed:.2f} s)"
        )
        if args.no_records:
            continue
        params = {"impl": args.impl, "threads": args.threads, "iters": args.iters, "gil": gil}
        record = records.new_record("K4", "race", f"{args.impl}-t{args.threads}", params, repeat)
        records.add_metric(record, "lost_updates", "count", value=lost)
        records.add_metric(record, "lost_dict_updates", "count", value=lost_dict)
        records.add_metric(record, "duration", "s", value=round(elapsed, 4))
        records.write_record(record, args.results)
    print(f"  trials with lost updates: {lost_runs}/{args.repeats}")


if __name__ == "__main__":
    main()
