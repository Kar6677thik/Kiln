"""Interpreter shutdown: atexit runs last-in-first-out, then finalization, unless os._exit.

We run the same tiny program three ways in a fresh interpreter: ending normally, ending
with os._exit(0), and with Instagram's trick (atexit.register(os._exit, 0) registered FIRST).
"""

import subprocess
import sys

PROGRAM = """
import atexit, functools, os, sys
say = functools.partial(print, flush=True)  # flush at once: os._exit throws unflushed output away
{trick}
class Resource:
    def __del__(self):
        say("  finalizer: Resource.__del__ ran during shutdown")
keep = Resource()              # a module-level object, freed only at interpreter teardown
atexit.register(say, "  atexit: handler registered FIRST runs LAST")
atexit.register(say, "  atexit: handler registered SECOND runs FIRST")
say("  main: done")
{ending}
"""

for label, trick, ending in (
    ("normal end", "", ""),
    ("os._exit(0) at the end", "", "os._exit(0)"),
    ("Instagram's trick", "atexit.register(os._exit, 0)", ""),
):
    print(f"{label}:")
    result = subprocess.run(
        [sys.executable, "-c", PROGRAM.format(trick=trick, ending=ending)],
        capture_output=True,
        text=True,
        check=False,
    )
    print(result.stdout, end="")
    print(f"  exit code {result.returncode}")
