"""A stand-in for "a library that quietly switches the garbage collector back on".

WHY THIS MODULE EXISTS
    LAB: this module misbehaves ON PURPOSE. Instagram found that msgpack re-enabled the GC
    they had disabled. Gate G3.1 checked today's msgpack (1.2.4, on 3.13–3.15t): it no longer
    does (errata E-03), so the K3 lab needs a culprit it controls. Provenance: written for
    Kiln; it imitates the reported behaviour, not any library's code.

    KILN_ROGUE_MODE chooses how it misbehaves:
      python   gc.enable() at import time               (catchers: shim A, import-flip B, C)
      capi     the C function PyGC_Enable() at import   (B, C, gdb; bypasses the shim)
      runtime  nothing at import; parse() calls PyGC_Enable() later (C, gdb)
      (unset)  behaves itself

HOW IT FITS
    examples/k3_app.py ──import rogue_gc_enabler──▶ maybe flips the GC
    task k3.parse ──rogue_gc_enabler.parse(data)──▶ maybe flips the GC (runtime mode)
    kiln.memory.gcwatch layers A/B/C ──▶ `gc_reenabled` events in the log

READ FIRST
    docs/concepts/global-interpreter-state.md, docs/python/ctypes-basics.md

MILESTONE
    Introduced in K3.
"""

import ctypes
import gc
import json
import os

MODE = os.environ.get("KILN_ROGUE_MODE", "")


def _c_enable() -> None:
    """Call the C-API function PyGC_Enable() directly, the way a C extension would.

    Why:
        A C extension doesn't look up `gc.enable` on the gc module; it calls the C function.
        ctypes.pythonapi gives us the same function pointer, so the shim (layer A) never
        sees this call (gate G3.8).
    """
    # LAB: flips process-wide GC state from "C", bypassing Python-level hooks on purpose.
    enable = ctypes.pythonapi.PyGC_Enable
    enable.restype = ctypes.c_int  # WHY: PyGC_Enable returns the previous state (0 or 1)
    enable()


if MODE == "python":
    gc.enable()  # LAB: re-enables the GC at import time, like the old msgpack did
elif MODE == "capi":
    _c_enable()


def parse(data: str) -> object:
    """Decode JSON text, and (in runtime mode) switch the GC on as a side effect.

    Why:
        The hardest case: the flip happens long after every import, inside a normal-looking
        function call, from "C". Only the runtime poll (layer C) or gdb can see it.

    Args:
        data: JSON text.

    Returns:
        The decoded value.
    """
    if MODE == "runtime":
        _c_enable()
    return json.loads(data)
