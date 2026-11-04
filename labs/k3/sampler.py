"""Sample the memory of a Kiln master and its workers every 200 ms, from a separate process.

WHY THIS MODULE EXISTS
    The K3 lab watches shared memory decay over time. Reading memory from *inside* a worker
    would itself allocate and disturb what we measure, so spec/10 §7 says: sample from a
    separate process. This one reads /proc/<pid>/smaps_rollup for the master and all its
    children (and the forkserver's children) and appends one JSON line per process per tick.

HOW IT FITS
    labs/k3/k3_lab.py ──starts──▶ python labs/k3/sampler.py --master PID --out FILE
        every 200 ms: kiln.memory.memtable.collect([], master) ──▶ JSONL rows
    the driver stops it (SIGTERM) and turns the rows into ExperimentRecord samples

READ FIRST
    docs/spec/10-measurement-methodology.md §7, docs/concepts/smaps.md

MILESTONE
    Introduced in K3.
"""

import argparse
import json
import signal
import time
from pathlib import Path
from types import FrameType

from kiln.memory import memtable

RUNNING = {"on": True}


def _stop(signum: int, frame: FrameType | None) -> None:
    """SIGTERM handler: finish the current tick, then exit.

    Args:
        signum: the signal number.
        frame: the interrupted frame (unused).
    """
    RUNNING["on"] = False


def main() -> None:
    """Sample until SIGTERM or until the master is gone.

    Why:
        A fixed interval with a monotonic clock gives evenly spaced samples; flushing each
        line means nothing is lost if the driver stops us abruptly.
    """
    parser = argparse.ArgumentParser(description="K3 memory sampler")
    parser.add_argument("--master", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=0.2)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, _stop)
    started = time.monotonic()
    with args.out.open("a", encoding="utf-8") as out:
        while RUNNING["on"] and Path(f"/proc/{args.master}").exists():
            t_s = round(time.monotonic() - started, 3)
            for row in memtable.collect([], args.master):
                r = row.rollup
                line = {
                    "t_s": t_s,
                    "pid": row.pid,
                    "role": row.role,
                    "pss": r.pss,
                    "pss_anon": r.pss_anon,
                    "shared": r.shared,
                    "private_dirty": r.private_dirty,
                    "rss": r.rss,
                }
                out.write(json.dumps(line) + "\n")
            out.flush()
            time.sleep(max(0.0, args.interval - (time.monotonic() - started - t_s)))


if __name__ == "__main__":
    main()
