# requires: py>=3.14
"""The same string literal: mortal on the first run, immortal on the second.

We write a tiny module to a temporary folder and import it in two fresh interpreters. The
first import compiles the source (and writes a .pyc); the second loads that .pyc. On the
GIL build, strings interned while *loading a .pyc* are made immortal; strings interned
while compiling source are not.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

folder = Path(tempfile.mkdtemp())
(folder / "mymodule.py").write_text('GREETING = "hello_from_my_module"\n')
CHECK = (
    "import sys; sys.path.insert(0, sys.argv[1]); import mymodule; "
    "print(sys._is_immortal(mymodule.GREETING))"
)
for run in ("first run (compiled from source)", "second run (loaded from .pyc)"):
    out = subprocess.run(
        [sys.executable, "-c", CHECK, str(folder)], capture_output=True, text=True, check=True
    ).stdout.strip()
    print(f"{run:34} literal immortal: {out}")
shutil.rmtree(folder)
