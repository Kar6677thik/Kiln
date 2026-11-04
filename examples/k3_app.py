"""The K3 lab app: a tiny app that imports a library which may re-enable the GC.

WHY THIS MODULE EXISTS
    Instagram's GC was switched back on by a library their app imported (msgpack). This app
    reproduces that situation with labs/k3/rogue_gc_enabler.py, whose behaviour is chosen by
    KILN_ROGUE_MODE (python / capi / runtime / unset). Run it with
    `kiln serve examples.k3_app:app --labs` and labs.preload_mb, worker.gc_mode and
    labs.gc_watch set through KILN_* environment variables.

HOW IT FITS
    kiln serve examples.k3_app:app ──import──▶ this module ──import──▶ rogue_gc_enabler
    task k3.parse ──▶ rogue_gc_enabler.parse() (the runtime culprit)

READ FIRST
    docs/labs/K3-cow.md, docs/concepts/global-interpreter-state.md

MILESTONE
    Introduced in K3.
"""

import atexit
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import kiln

# WHY: the rogue module lives with the lab scripts in labs/k3/, which isn't a package; put
# that folder on the import path so `import rogue_gc_enabler` finds it in every start method.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "labs" / "k3"))

import rogue_gc_enabler  # WHY: must come after the sys.path change above

app = kiln.App("k3")
MARKS = os.environ.get("KILN_K3_MARK_DIR")  # lab (d): record whether exit handlers ran


def _mark(  # WHY: the defaults are captured functions, see Why
    kind: str,
    _join: Callable[..., str] = os.path.join,
    _open: Callable[..., Any] = open,
    _getpid: Callable[[], int] = os.getpid,
    _marks: str | None = MARKS,
) -> None:
    """Leave an empty file named <kind>-<pid> in MARKS.

    Why:
        Lab (d) checks whether a worker's atexit handler and finalizers ran at exit. Default
        arguments keep the functions alive during interpreter teardown, when globals vanish.

    Args:
        kind: "atexit" or "del".
    """
    with _open(_join(_marks or ".", f"{kind}-{_getpid()}"), "w"):
        pass


class _Garbage:
    """An object whose __del__ marks that a garbage collection freed it.

    Why:
        Lab (d) needs to see whether interpreter finalization (its final collection) ran.
    """

    def __del__(self, _mark=_mark) -> None:  # noqa: ANN001  # WHY: a captured function
        """Mark that a collection freed this object.

        Args:
            _mark: captured at class creation, because the global may be gone by now.
        """
        _mark("final_gc")


def _leave_garbage() -> None:
    """Atexit handler: create an unreachable cycle, then mark that atexit ran.

    Why:
        After the atexit handlers, only the interpreter's final collection can free this
        cycle, and os._exit skips it. (Module teardown is no reliable probe: Python doesn't
        promise __del__ for objects still alive at exit.)
    """
    cycle = _Garbage()
    cycle.me = cycle  # type: ignore[attr-defined]  # WHY: a cycle needs the GC to be freed
    del cycle
    _mark("atexit")


if MARKS:
    atexit.register(_leave_garbage)


@app.task(name="k3.parse")
def parse(data: str = "[1, 2, 3]") -> dict[str, object]:
    """Decode JSON through the rogue library (in runtime mode it switches the GC on).

    Args:
        data: JSON text.

    Returns:
        {"value": the decoded value, "pid": this worker's pid}.
    """
    return {"value": rogue_gc_enabler.parse(data), "pid": os.getpid()}
