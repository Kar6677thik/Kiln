"""Entry point for `python -m kiln`.

WHY THIS MODULE EXISTS
    `python -m kiln` asks Python to run the package `kiln` as a program. Python looks for a
    file named `__main__.py` inside the package and runs it. This lets you use Kiln even when
    the `kiln` command isn't on your PATH, for example with a specific interpreter:
    `~/.local/bin/python3.13 -m kiln --version`.

HOW IT FITS
    python -m kiln ──runs──▶ this file ──calls──▶ kiln.cli.main()

READ FIRST
    docs/python/running-python-and-the-repl.md ("python -m")

MILESTONE
    Introduced in K0.
"""

# PY: "import sys" gives us access to interpreter details such as sys.exit().
import sys

# PY: "from X import Y" imports one name (main) from the module kiln.cli.
from kiln.cli import main

# PY: __name__ is "__main__" only when this file is the program being run (not imported).
# See docs/python/running-python-and-the-repl.md.
if __name__ == "__main__":
    # WHY: main() returns an exit code (0 = success); sys.exit() hands it to the operating
    # system so scripts and `echo $?` can see whether the command worked.
    sys.exit(main())
