"""The `worker.gc_mode` strategies: what Kiln does to Python's garbage collector, and when.

WHY THIS MODULE EXISTS
    After fork, the master and its workers share memory pages until one of them writes to a
    page (copy-on-write). CPython's cycle garbage collector writes into the header of every
    container object it scans, so one full collection in a worker can turn hundreds of
    shared MiB into private copies. That's the Instagram incident
    (docs/concepts/copy-on-write.md). Each mode below is one of the known answers; K3's lab
    measures them side by side.

HOW IT FITS
    mode        apply_parent_early()        before_fork()     apply_child_early()
    default     -                           -                 -
    disabled    gc.disable()                -                 gc.disable()
    threshold0  gc.set_threshold(0)         -                 gc.set_threshold(0)
    freeze      gc.disable()                gc.freeze()       gc.enable()

    master/arbiter.py and launch/forkserver_main.py ──apply_parent_early()── before preloading
    launch/fork.py fork_child() ──before_fork()── right before os.fork()
    worker/main.py run_worker() ──apply_child_early()── right after signal setup

READ FIRST
    docs/concepts/garbage-collector.md, docs/concepts/gc-freeze.md,
    docs/concepts/copy-on-write.md, docs/python/gc-module.md

MILESTONE
    Introduced in K3.
"""

import gc
import logging
from collections.abc import Callable

from kiln import logsetup
from kiln.memory import gcwatch

log = logging.getLogger(__name__)
# LAB: process-wide on purpose: whether this process already froze before a fork.
_FROZEN = {"done": False}
MODES = ("default", "disabled", "threshold0", "freeze")


def _applied(phase: str, mode: str) -> None:
    """Log the GC state after a hook ran (event `gc_mode_applied`, DEBUG).

    Why:
        GC state is invisible global state; the log makes every change traceable.

    Args:
        phase: "parent_early", "before_fork" or "child_early".
        mode: the gc_mode.
    """
    logsetup.event(
        log,
        logging.DEBUG,
        "gc_mode_applied",
        phase=phase,
        mode=mode,
        enabled=gc.isenabled(),
        threshold=",".join(str(n) for n in gc.get_threshold()),
        frozen=gc.get_freeze_count(),
    )


def apply_parent_early(mode: str) -> None:
    """In the master (or forkserver), before the app and the preload graph are loaded.

    Why:
        For `freeze`, the docs' recipe says *disable early in the parent*: if a collection
        ran between building the shared data and freezing it, it would leave "holes" in
        memory pages that later allocations (in the children) fill, dirtying shared pages.
        For `disabled`/`threshold0`, the parent simply never collects.

    Args:
        mode: the gc_mode.
    """
    if mode in ("disabled", "freeze"):
        gc.disable()
    elif mode == "threshold0":
        # WHY: set_threshold(0) turns off *automatic* collection even if some library later
        # calls gc.enable(), the reason Instagram switched to it from gc.disable().
        gc.set_threshold(0)
    if mode != "default":
        _applied("parent_early", mode)


def before_fork(mode: str) -> None:
    """Right before the FIRST os.fork() of this process: freeze every object that exists now.

    Why:
        gc.freeze() moves all tracked objects to a "permanent generation" the collector
        never scans again. Done right before fork, the child inherits that: its collections
        skip everything the parent built, so they don't write to those shared pages.

        Only once per process: on the free-threaded build (3.15t), gc.freeze() sets a bit in
        *every* object's header. Freezing again before the second fork made the parent
        rewrite pages it already shared with the first worker, so that worker ended up with
        ~62 MiB of private copies (K3 lab (a), ADR-036). On the GIL build freeze only moves
        list pointers, but there is nothing to gain from repeating it either.

    Args:
        mode: the gc_mode.
    """
    if mode == "freeze" and not _FROZEN["done"]:
        gc.freeze()
        _FROZEN["done"] = True
        _applied("before_fork", mode)


def apply_child_early(mode: str, start_method: str) -> None:
    """In a worker, right after signal setup and before the app is loaded.

    Why:
        A forked worker inherits the parent's GC state, but a spawned one starts fresh, so
        `disabled` and `threshold0` must be applied again here. Under `freeze` the worker
        re-enables the GC: its *own* new objects should be collected normally; only the
        frozen, shared ones are skipped. Under spawn nothing is shared or frozen, so
        `freeze` behaves like `default` (the master warns once).

    Args:
        mode: the gc_mode.
        start_method: fork, spawn or forkserver.
    """
    if mode == "disabled":
        gc.disable()
    elif mode == "threshold0":
        gc.set_threshold(0)
    elif mode == "freeze" and start_method != "spawn":
        gc.enable()
    if mode != "default":
        _applied("child_early", mode)


# LAB: the watch installed in this process, if any (code-style §6 exception: a forked worker
# inherits its parent's watch and must replace it with its own).
ACTIVE_WATCH: gcwatch.GcWatch | None = None


def log_gc_event(event: gcwatch.GcEvent, job_id: str | None = None) -> None:
    """Log a GcWatch sighting as the WARNING event `gc_reenabled` (spec/06).

    Why:
        gcwatch.py may not import Kiln (K7 loads it standalone), so logging lives here.

    Args:
        event: the sighting.
        job_id: the job running at the time (workers), if any.
    """
    logsetup.event(
        log,
        logging.WARNING,
        "gc_reenabled",
        layer=event.layer,
        module=event.module,
        job_id=job_id or event.job_id,
        note=event.note,
        stack=" | ".join(event.stack),
    )


def install_watch(
    mode: str, *, in_parent: bool, on_event: Callable[[gcwatch.GcEvent], None]
) -> gcwatch.GcWatch | None:
    """Install the "who re-enabled the GC?" detector for this process, if the mode needs it.

    Why:
        What counts as suspicious depends on the mode: under `disabled` (and in a `freeze`
        parent) the GC must stay off; under `threshold0` it is on but never automatic, so a
        gc.enable() call is harmless and only reported; a `freeze` child turns it on itself.

    Args:
        mode: the gc_mode.
        in_parent: True in the master or forkserver, False in a worker.
        on_event: receives every sighting.

    Returns:
        The installed watch, or None under `default`.
    """
    global ACTIVE_WATCH  # WHY: one watch per process, replaced after fork
    if ACTIVE_WATCH is not None:
        ACTIVE_WATCH.uninstall()  # WHY: a forked worker inherited the parent's watch
        ACTIVE_WATCH = None
    if mode == "default":
        return None
    if mode == "threshold0":
        watch = gcwatch.GcWatch(
            expect_disabled=False,
            on_event=on_event,
            note="harmless under threshold0: automatic collections stay off",
        )
    elif mode == "freeze" and not in_parent:
        watch = gcwatch.GcWatch(
            expect_disabled=False,
            on_event=on_event,
            note="informational: freeze workers run with the GC on by design",
        )
    else:
        watch = gcwatch.GcWatch(expect_disabled=True, on_event=on_event)
    watch.install()
    ACTIVE_WATCH = watch
    return watch
