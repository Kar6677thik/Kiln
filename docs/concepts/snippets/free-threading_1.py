"""Which build is this, is the GIL on, and what does PYTHON_GIL do?"""

import os
import subprocess
import sys
import sysconfig

PROBE = "import sys; print(sys._is_gil_enabled())"
free_threaded = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
print(
    f"Py_GIL_DISABLED = {sysconfig.get_config_var('Py_GIL_DISABLED')} -> free-threaded build: {free_threaded}"
)
print(f"sys._is_gil_enabled() right now: {sys._is_gil_enabled()}")
if free_threaded:
    for value in ("0", "1"):
        env = {**os.environ, "PYTHON_GIL": value}
        out = subprocess.run([sys.executable, "-c", PROBE], env=env, capture_output=True, text=True)
        print(
            f"PYTHON_GIL={value}: a new interpreter starts with GIL enabled = {out.stdout.strip()}"
        )
else:
    env = {**os.environ, "PYTHON_GIL": "0"}
    out = subprocess.run([sys.executable, "-c", PROBE], env=env, capture_output=True, text=True)
    print(f"PYTHON_GIL=0 on this build: exit code {out.returncode}")
    print(f"  {out.stderr.strip().splitlines()[0]}")
