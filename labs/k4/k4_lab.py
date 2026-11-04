"""K4 lab driver: one entry point per experiment, so `dev.sh lab k4-<name>` works.

WHY THIS MODULE EXISTS
    scripts/dev.sh runs labs/k<N>/k<N>_lab.py <experiment> [args]. K4's experiments live in
    their own scripts (race_demo.py, cpu_io_bench.py, the GIL demo); this file forwards to them.

HOW IT FITS
    ./scripts/dev.sh lab k4-race  [args] → labs/k4/race_demo.py [args]
    ./scripts/dev.sh lab k4-bench [args] → labs/k4/cpu_io_bench.py [args]
    ./scripts/dev.sh lab k4-gil   [args] → labs/k4/gil_demo.py [args]

READ FIRST
    docs/labs/K4-concurrency.md

MILESTONE
    Introduced in K4.
"""

import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = {"race": "race_demo.py", "bench": "cpu_io_bench.py", "gil": "gil_demo.py"}


def main() -> None:
    """Run the named experiment's script with the remaining arguments.

    Why:
        runpy runs the script exactly as `python script.py` would, with its own __main__.
    """
    if len(sys.argv) < 2 or sys.argv[1] not in SCRIPTS:
        raise SystemExit(f"usage: k4_lab.py {{{','.join(SCRIPTS)}}} [args]")
    script = HERE / SCRIPTS[sys.argv[1]]
    sys.argv = [str(script), *sys.argv[2:]]
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
