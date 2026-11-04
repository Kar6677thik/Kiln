"""Do threads speed up pure-Python CPU work? And do they speed up waiting?

Runs the same CPU-bound loop once on 1 thread, then on 4 threads at the same time, and
compares the wall-clock time. Then 4 threads each sleep 0.2 s. Run it on 3.15 and on 3.15t.
"""

import sys
import sysconfig
import threading
import time

N = 5_000_000  # loop iterations per thread (about 0.15 s of pure Python)


def burn() -> None:
    """Pure-Python arithmetic: holds the GIL the whole time on a GIL build."""
    total = 0
    for i in range(N):
        total += i * i


def run_threads(target: object, count: int) -> float:
    """Start `count` threads running `target`, wait for all, return the seconds taken."""
    threads = [threading.Thread(target=target) for _ in range(count)]  # type: ignore[arg-type]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return time.perf_counter() - started


build = "free-threaded" if sysconfig.get_config_var("Py_GIL_DISABLED") else "GIL"
print(f"build: {build}, GIL enabled now: {getattr(sys, '_is_gil_enabled', lambda: True)()}")
print(f"switch interval: {sys.getswitchinterval()} s")
one = run_threads(burn, 1)
four = run_threads(burn, 4)
print(f"CPU work, 1 thread : {one:.2f} s")
print(f"CPU work, 4 threads: {four:.2f} s  (4x the work; {4 * one / four:.1f}x speed-up)")
sleeping = run_threads(lambda: time.sleep(0.2), 4)
print(f"4 threads each sleeping 0.2 s: {sleeping:.2f} s in total")
