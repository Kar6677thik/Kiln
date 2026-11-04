"""Which installed modules turn the GIL back on? An informational scan of the active venv.

WHY THIS MODULE EXISTS
    The GIL-re-enabling trap usually comes from a dependency you didn't write. This script
    imports each top-level module installed in the current environment, one per FRESH
    subprocess (once the GIL is on it stays on), on a free-threaded interpreter, and reports
    which ones flip it. Results change month to month as projects add free-threading support
    (ADR-015), so the lab report prints them dated and labelled "informational".

HOW IT FITS
    KILN_PY=3.15t ./scripts/dev.sh run labs/k4/scan_gil.py
        site-packages top-level names ──▶ python -c "import X" ──▶ GIL before/after per name

READ FIRST
    docs/concepts/free-threading.md

MILESTONE
    Introduced in K4.
"""

import json
import subprocess
import sys
import sysconfig
from pathlib import Path

PROBE = (
    "import sys, json, importlib; b = sys._is_gil_enabled(); "
    "importlib.import_module(sys.argv[1]); print(json.dumps([b, sys._is_gil_enabled()]))"
)


def top_level_names(site: Path) -> list[str]:
    """The importable top-level module names in a site-packages folder.

    Why:
        Each distribution's *.dist-info/top_level.txt (when present) lists them; otherwise
        we fall back to the folders and .py/.so files themselves.

    Args:
        site: the site-packages folder.

    Returns:
        Sorted names, without private (_name) ones.
    """
    names: set[str] = set()
    for entry in site.iterdir():
        stem = entry.name.split(".")[0]
        if entry.name.endswith((".dist-info", ".pth")) or stem.startswith("_"):
            continue
        if (entry.is_dir() and (entry / "__init__.py").exists()) or entry.suffix in (".py", ".so"):
            names.add(stem)
    return sorted(names)


def main() -> None:
    """Scan and print one line per module.

    Why:
        A plain list is what the lab report quotes.
    """
    if getattr(sys, "_is_gil_enabled", lambda: True)():
        print("run this on a free-threaded interpreter (KILN_PY=3.15t); the GIL is on already")
        return
    site = Path(sysconfig.get_paths()["purelib"])
    print(f"== GIL scan of {site} on {sys.version.split()[0]}")
    for name in top_level_names(site):
        done = subprocess.run(
            [sys.executable, "-W", "ignore", "-c", PROBE, name],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if done.returncode != 0:
            print(f"  {name:24} import failed")
            continue
        before, after = json.loads(done.stdout.strip().splitlines()[-1])
        verdict = "RE-ENABLES THE GIL" if after and not before else "keeps it off"
        print(f"  {name:24} {verdict}")


if __name__ == "__main__":
    main()
