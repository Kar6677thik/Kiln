"""Instagram's shutdown trick: end the process with os._exit from the *last* atexit handler.

WHY THIS MODULE EXISTS
    When a Python process exits normally, it runs the atexit handlers, then *finalizes the
    interpreter*: a last garbage collection and a teardown of every module. In a worker that
    shares hundreds of MiB with its parent, that final pass touches every object, copying
    pages just before the process dies, and it takes time. Instagram skipped it with
    `atexit.register(os._exit, 0)` (docs/concepts/finalization-and-atexit.md).

    atexit runs handlers last-in-first-out, so a handler registered FIRST runs LAST: after the
    app's own handlers, but before finalization, which os._exit skips entirely.

HOW IT FITS
    worker/__main__.py (spawned workers) and the arbiter (master) ──install()── first thing
    ... the process runs ... ──set_exit_code(code)──▶ sys.exit(code)
    atexit: app handlers ... ──▶ _os_exit_with_code() ──os._exit(code)──▶ no finalization
    Forked workers never get here: they already end with os._exit (ADR-028).

READ FIRST
    docs/concepts/finalization-and-atexit.md, docs/python/atexit.md

MILESTONE
    Introduced in K3.
"""

import atexit
import os
import sys

# LAB: process-wide state on purpose (code-style §6 exception): an atexit handler can't
# receive arguments at exit time, so it reads these.
_STATE = {"enabled": False, "code": 0}


def _os_exit_with_code() -> None:
    """The atexit handler: flush our output, then leave without finalizing.

    Why:
        os._exit skips Python's own buffer flushing too, so we flush stdout and stderr
        first, or the last lines of output would be lost.
    """
    if not _STATE["enabled"]:
        return
    sys.stdout.flush()
    sys.stderr.flush()
    # SYSCALL: os._exit → _exit(2): the process ends here; no GC, no module teardown.
    os._exit(int(_STATE["code"]))


def install() -> None:
    """Register the handler. Call it as early as possible, before the app is imported.

    Why:
        The earlier it is registered, the later it runs (LIFO), so every handler the app
        registers afterwards still runs first.
    """
    if not _STATE["enabled"]:
        _STATE["enabled"] = True
        atexit.register(_os_exit_with_code)


def set_exit_code(code: int) -> None:
    """Remember the code the process is about to exit with.

    Why:
        sys.exit(code) passes the code to the interpreter, not to atexit handlers; our
        handler needs it to call os._exit with the same code.

    Args:
        code: the exit code.
    """
    _STATE["code"] = code
