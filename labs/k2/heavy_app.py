"""A deliberately heavy app for lab K2 (a): importing it allocates 100 MiB.

WHY THIS MODULE EXISTS
    Lab K2 (a) asks how much memory each start method costs per worker. With a tiny app the
    answer is "the interpreter", the same everywhere. With an app that holds 100 MiB at import
    time, the methods differ sharply: fork and forkserver import it once and share the pages
    with every worker (copy-on-write), while spawn makes every worker import its own copy.
    K3 will add a proper `labs.preload_mb` setting; this file is the K2 stand-in that the
    milestone spec allows (docs/milestones/K2-start-methods-forkcheck.md §7 (a)).

HOW IT FITS
    labs/k2/k2_lab.py ──kiln serve heavy_app:app (app.pythonpath = labs/k2)──▶ this module

READ FIRST
    docs/concepts/start-methods.md, docs/concepts/memory-metrics.md, docs/concepts/import-system.md

MILESTONE
    Introduced in K2.
"""

import os

import kiln

# WHY: os.urandom, not bytes(n): a zero-filled buffer may never be backed by real memory pages
# (the kernel maps untouched zero pages lazily), so it wouldn't show up in RSS or PSS at all.
# Random bytes force all 100 MiB to be really allocated and written.
# GOTCHA: a single bytes object is shared perfectly after fork, because only its small header
# (with the reference count) is ever written. A list of millions of small objects would not
# be: see K3's copy-on-write lab.
BLOB = os.urandom(100 * 1024 * 1024)

app = kiln.App("heavy")


@app.task
def blob_size() -> int:
    """Return the size of the preloaded blob.

    Why:
        A task that touches the module-level data proves the worker really has it.

    Returns:
        The number of bytes (104857600).
    """
    return len(BLOB)
