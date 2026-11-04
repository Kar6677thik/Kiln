"""A compound dict update from 8 threads: the dict survives, but updates are lost.

Each thread does d[k] = d.get(k, 0) + 1 many times on a few shared keys. Each single dict
operation is safe; the get-then-set pair is not. Run it on 3.15 and on 3.15t.
"""

import sysconfig
import threading

THREADS = 8
ITERS = 50_000
KEYS = ("a", "b", "c", "d")


def worker(shared: dict[str, int], barrier: threading.Barrier) -> None:
    """Increment each key ITERS / len(KEYS) times with a get-then-set."""
    barrier.wait()
    for i in range(ITERS):
        key = KEYS[i % len(KEYS)]
        shared[key] = shared.get(key, 0) + 1  # two dict operations: get, then set


shared: dict[str, int] = {}
barrier = threading.Barrier(THREADS)
threads = [threading.Thread(target=worker, args=(shared, barrier)) for _ in range(THREADS)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
build = "free-threaded" if sysconfig.get_config_var("Py_GIL_DISABLED") else "GIL"
expected = THREADS * ITERS
print(f"build: {build}")
print(f"keys still intact: {sorted(shared) == sorted(KEYS)}; len(shared) = {len(shared)}")
print(
    f"total = {sum(shared.values()):,} of {expected:,}; lost = {expected - sum(shared.values()):,}"
)
