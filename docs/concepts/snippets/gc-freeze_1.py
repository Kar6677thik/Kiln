# requires: linux
"""What gc.freeze() fixes, and what it doesn't (errata E-15).

The parent builds 300,000 dicts, then follows the docs' recipe (gc.disable early,
gc.freeze before fork, gc.enable in the child). A forked child then:
  1. runs a full collection: with the objects frozen, it doesn't scan them,
  2. READS every dict: frozen or not, each read writes the dict's refcount.
For comparison, the first child is forked WITHOUT freeze. Each child prints how many MiB
of shared pages it had to copy (its Private_Dirty growth).
"""

import gc
import os


def private_dirty_mib() -> float:
    """This process's Private_Dirty from /proc/self/smaps_rollup, in MiB."""
    for line in open("/proc/self/smaps_rollup"):
        if line.startswith("Private_Dirty:"):
            return int(line.split()[1]) / 1024
    return 0.0


def child(label: str) -> None:
    """Fork a child that collects, then reads everything, printing its copied memory."""
    pid = os.fork()
    if pid == 0:
        start = private_dirty_mib()
        gc.enable()  # recipe step 3: re-enable early in the child
        gc.collect()  # (enabling may already trigger an automatic collection; we count both)
        after_gc = private_dirty_mib()
        sum(len(d) for d in data)  # "just reading": every dict is referenced once
        after_read = private_dirty_mib()
        print(
            f"{label:16} frozen={gc.get_freeze_count():>7}  gc copied {after_gc - start:5.1f} MiB,"
            f" then reading copied {after_read - after_gc:5.1f} MiB more",
            flush=True,
        )
        os._exit(0)
    os.waitpid(pid, 0)


gc.disable()  # recipe step 1: no collections while we build (they'd shuffle generations)
data = [{"id": i, "name": f"user{i}"} for i in range(300_000)]
child("without freeze")
gc.freeze()  # recipe step 2: right before fork, move everything to the permanent generation
child("with freeze")
gc.unfreeze()
gc.enable()
