"""Module code runs once per process: a second import is a dictionary lookup in sys.modules."""

import pathlib
import sys
import tempfile

with tempfile.TemporaryDirectory() as folder:
    (pathlib.Path(folder) / "noisy.py").write_text(
        'print("  noisy.py is executing its top-level code")\nVALUE = 42\n'
    )
    sys.path.insert(0, folder)
    print("first import:")
    import noisy

    print("second import (nothing printed, it comes from sys.modules):")
    import noisy as again

    print("same object both times?", noisy is again)
    print("cached in sys.modules?", "noisy" in sys.modules)
    del sys.modules["noisy"]
    print("after deleting it from sys.modules, importing runs the file again:")
    import noisy

    sys.path.remove(folder)
