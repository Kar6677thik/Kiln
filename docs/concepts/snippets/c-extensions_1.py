"""C extensions are .so files; their file names carry the build's ABI tag.

Also: importing standard-library C extensions on a free-threaded build keeps the GIL off,
because each one declares that it doesn't need it (the Py_mod_gil slot).
"""

import importlib
import sys
import sysconfig
from pathlib import Path

print(f"extension file suffix (EXT_SUFFIX): {sysconfig.get_config_var('EXT_SUFFIX')}")
print(f"Python.h present: {(Path(sysconfig.get_paths()['include']) / 'Python.h').exists()}")
for name in ("_json", "_dbm"):
    module = importlib.import_module(name)
    where = Path(getattr(module, "__file__", "") or "(built into the interpreter)").name
    print(f"{name:9} -> {where}")
print(f"GIL enabled after those imports: {sys._is_gil_enabled()}")
