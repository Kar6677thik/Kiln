"""Without the __main__ guard, a spawn child re-runs the process-starting code and Python stops it."""

import pathlib
import subprocess
import sys
import tempfile

UNGUARDED = """
import multiprocessing as mp
def work():
    print("work ran")
mp.set_start_method("spawn", force=True)
p = mp.Process(target=work)
p.start()
p.join()
"""

with tempfile.TemporaryDirectory() as folder:
    script = pathlib.Path(folder) / "unguarded.py"
    script.write_text(UNGUARDED)
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=30
    )
    lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
    start = next(i for i, line in enumerate(lines) if line.startswith("RuntimeError"))
    print("the spawn child crashed with RuntimeError:")
    for line in lines[start + 1 : start + 4]:
        print("   ", line)
    print("the parent still printed:", result.stdout.strip() or "(nothing)")
