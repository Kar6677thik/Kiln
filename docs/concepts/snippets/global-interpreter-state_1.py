"""Any library can flip global interpreter state. Three ways to notice: shim, import hook, poll.

We write two throwaway "libraries" to a temp folder: one calls gc.enable() from Python, the
other calls the C function PyGC_Enable() through ctypes (as a C extension would). Then we
watch with (A) a shim replacing gc.enable, (B) a sys.meta_path hook comparing
gc.isenabled() before and after each import, and (C) a plain poll of gc.isenabled().
"""

import gc
import importlib.abc
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

folder = Path(tempfile.mkdtemp())
(folder / "pylib.py").write_text("import gc\ngc.enable()  # a Python library re-enabling the GC\n")
(folder / "clib.py").write_text(
    "import ctypes\nctypes.pythonapi.PyGC_Enable()  # what C code does: no Python gc.enable call\n"
)
sys.path.insert(0, str(folder))

# (A) the shim: replace the module attribute gc.enable with a wrapper that records who called.
original_enable = gc.enable


def shim_enable() -> None:
    """Record the caller, then really enable the GC."""
    caller = traceback.extract_stack()[-2]
    print(f"  A shim: gc.enable() called from {Path(caller.filename).name}:{caller.lineno}")
    original_enable()


gc.enable = shim_enable


# (B) the import hook: wrap every loader so we can compare the GC state around exec_module.
class FlipWatcher(importlib.abc.MetaPathFinder):
    """A finder that finds nothing itself; it wraps the loader the other finders return."""

    def find_spec(self, name, path, target=None):  # type: ignore[no-untyped-def]
        """Ask the remaining finders, then wrap the loader's exec_module."""
        for finder in sys.meta_path[1:]:
            spec = finder.find_spec(name, path, target) if hasattr(finder, "find_spec") else None
            if spec is not None and hasattr(spec.loader, "exec_module"):
                real_exec = spec.loader.exec_module

                def exec_module(module, real_exec=real_exec, name=name):  # type: ignore[no-untyped-def]
                    before = gc.isenabled()
                    real_exec(module)
                    if not before and gc.isenabled():
                        print(f"  B import hook: importing {name!r} turned the GC on")

                spec.loader.exec_module = exec_module  # type: ignore[method-assign]
                return spec
        return None


sys.meta_path.insert(0, FlipWatcher())

for library in ("pylib", "clib"):
    gc.disable()
    print(f"import {library} (GC disabled before):")
    __import__(library)
    print(f"  C poll: gc.isenabled() is now {gc.isenabled()}")

sys.meta_path.pop(0)
gc.enable = original_enable
gc.enable()
sys.path.remove(str(folder))
shutil.rmtree(folder)
