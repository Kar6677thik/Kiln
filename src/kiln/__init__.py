"""Kiln: a pre-fork job server built from scratch to learn how Python really works.

WHY THIS MODULE EXISTS
    A folder becomes a Python *package* when it contains an `__init__.py` file. Python runs
    this file the first time anything does `import kiln`. Whatever it defines becomes
    available as `kiln.<name>`. For now that's only the version number. K1 adds the public
    task API (`kiln.App`), see docs/spec/03-jobs-and-tasks.md §2.

HOW IT FITS
    kiln/__init__.py   ← you are here: the package's front door
    kiln/__main__.py   ← what `python -m kiln` runs
    kiln/cli.py        ← the `kiln` command-line interface
    kiln/doctor.py     ← `kiln doctor` environment checks
    kiln/bench/        ← measurement tools (ExperimentRecord files)

READ FIRST
    docs/python/modules-and-imports.md (packages and __init__.py)
    docs/spec/01-architecture.md §5 (the full module map)

MILESTONE
    Introduced in K0. Changed in: (K1 will add App and the task decorator)
"""

# PY: a plain module-level variable. By convention, `__version__` holds a package's
# version string; tools and humans read it as `kiln.__version__`.
# WHY: keep this in sync with `version` in pyproject.toml (a test checks they match).
__version__ = "0.1.0"

# The public task API (docs/spec/03-jobs-and-tasks.md §2): `app = kiln.App("demo")`.
# PY: re-exporting a name here lets users write kiln.App instead of kiln.app.App.
from kiln.app import App

__all__ = ["App", "__version__"]
