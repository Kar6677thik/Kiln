"""What spawn does to the main script: it runs the top-level code AGAIN, as `__mp_main__`.

We write a small script to a temporary folder and run it with Python. The script has an
unguarded print at the top and starts one spawn child.
"""

import pathlib
import subprocess
import sys
import tempfile

SCRIPT = """
import multiprocessing as mp
print(f"top-level code running, __name__ = {__name__!r}", flush=True)

def work():
    print(f"in the child, work() runs; this module is {__name__!r}", flush=True)

if __name__ == "__main__":
    mp.set_start_method("spawn")
    child = mp.Process(target=work)
    child.start()
    child.join()
"""

with tempfile.TemporaryDirectory() as folder:
    script = pathlib.Path(folder) / "demo_script.py"
    script.write_text(SCRIPT)
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=30
    )
    print(result.stdout, end="")
