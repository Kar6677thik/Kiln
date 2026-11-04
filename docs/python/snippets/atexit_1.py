"""atexit handlers run in reverse order of registration, and not at all after os._exit or SIGKILL."""

import subprocess
import sys

PROGRAM = """
import atexit, os, signal, sys
atexit.register(print, "handler A (registered first)")
atexit.register(print, "handler B (registered second)")
how = sys.argv[1]
if how == "sys.exit":
    sys.exit(3)
if how == "os._exit":
    os._exit(3)
if how == "SIGKILL":
    os.kill(os.getpid(), signal.SIGKILL)
"""

for how in ("end of script", "sys.exit", "os._exit", "SIGKILL"):
    result = subprocess.run(
        [sys.executable, "-c", PROGRAM, how], capture_output=True, text=True, check=False
    )
    printed = result.stdout.strip().replace("\n", " | ") or "(nothing)"
    print(f"{how:14} exit={result.returncode:>3}  printed: {printed}")
