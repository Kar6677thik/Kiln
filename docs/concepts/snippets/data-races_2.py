"""The GIL makes races rare, not impossible: the same race on a GIL build.

CPython only hands the GIL to another thread at certain points (function calls and loop
back-edges), every ~5 ms. `counter.value += 1` has no such point inside it, so on a GIL build
it is never split. A function call between the read and the write *is* such a point.
"""

import sysconfig
import threading

THREADS = 8
ITERS = 500_000  # long enough that each thread runs for more than one 5 ms time slice


class Counter:
    """A shared counter."""

    value = 0


def log_it(value: int) -> int:
    """Stand-in for any function call (logging, a metric...)."""
    return value


def plus_equals(counter: Counter, barrier: threading.Barrier) -> None:
    """counter.value += 1: no switch point between the read and the write on a GIL build."""
    barrier.wait()
    for _ in range(ITERS):
        counter.value += 1


def read_call_write(counter: Counter, barrier: threading.Barrier) -> None:
    """Read, call a function, write: the call is a point where the GIL can change hands."""
    barrier.wait()
    for _ in range(ITERS):
        current = counter.value
        log_it(current)
        counter.value = current + 1


def lost(worker: object) -> int:
    """Run 8 threads once; return how many increments were lost."""
    counter = Counter()
    barrier = threading.Barrier(THREADS)
    threads = [
        threading.Thread(target=worker, args=(counter, barrier))  # type: ignore[arg-type]
        for _ in range(THREADS)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return THREADS * ITERS - counter.value


build = "free-threaded" if sysconfig.get_config_var("Py_GIL_DISABLED") else "GIL"
print(f"build: {build}; expected total per trial: {THREADS * ITERS:,}")
print(f"+= 1             : lost in 2 trials: {[lost(plus_equals) for _ in range(2)]}")
print(f"read, call, write: lost in 2 trials: {[lost(read_call_write) for _ in range(2)]}")
