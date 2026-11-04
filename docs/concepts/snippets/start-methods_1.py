"""The same tiny program under fork, forkserver and spawn: what does the child see?

The parent changes a module-level variable at run time, then starts a child. Under fork the
child is a copy and sees the change; under forkserver and spawn the child re-imports this file
and sees the import-time value. We also time how long each start takes.
"""

import multiprocessing as mp
import time

SETTING = "import-time value"


def report(queue):
    queue.put(SETTING)


if __name__ == "__main__":
    SETTING = "changed by the parent at run time"
    for method in ("fork", "forkserver", "spawn"):
        context = mp.get_context(method)
        queue = context.Queue()
        started = time.perf_counter()
        child = context.Process(target=report, args=(queue,))
        child.start()
        seen = queue.get(timeout=10)
        child.join()
        elapsed_ms = (time.perf_counter() - started) * 1000
        print(f"{method:<10} child saw: {seen!r:<38} start+join took {elapsed_ms:6.1f} ms")
