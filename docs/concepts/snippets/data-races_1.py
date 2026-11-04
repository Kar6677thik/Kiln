"""A data race: 8 threads each add 1 to a shared counter, without a lock and with one.

A threading.Barrier makes all threads start at the same instant, so they really overlap.
Run it on 3.15 (GIL) and on 3.15t (free-threaded).
"""

import sysconfig
import threading

THREADS = 8
ITERS = 100_000


class Counter:
    """A shared counter."""

    def __init__(self) -> None:
        self.value = 0
        self.lock = threading.Lock()


def racy(counter: Counter, barrier: threading.Barrier) -> None:
    """Read, add, write: three steps another thread can interleave with."""
    barrier.wait()
    for _ in range(ITERS):
        counter.value += 1


def locked(counter: Counter, barrier: threading.Barrier) -> None:
    """The same, but only one thread at a time may do the three steps."""
    barrier.wait()
    for _ in range(ITERS):
        with counter.lock:
            counter.value += 1


def trial(worker: object) -> int:
    """Run the workers once; return how many increments were lost."""
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
for name, worker in (("racy", racy), ("locked", locked)):
    lost = [trial(worker) for _ in range(5)]
    print(f"{name:6}: lost updates in 5 trials: {lost}")
