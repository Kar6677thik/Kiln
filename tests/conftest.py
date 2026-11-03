"""Shared pytest configuration: environment-based skips and the free-threading sanity check.

WHY THIS MODULE EXISTS
    pytest automatically loads a file named `conftest.py` before running the tests next to
    it. Kiln's tests run on four interpreters (3.13, 3.14, 3.15, 3.15t) and on machines with
    or without a C compiler, cgroups, etc. Tests declare what they need with markers such as
    `@pytest.mark.requires_315`; this file turns each unmet need into a *skip with a reason*,
    so a skipped test always says why (docs/spec/09-testing-strategy.md §8).

HOW IT FITS
    pytest ──loads──▶ conftest.py ──hooks──▶ every collected test (skip or run)
                                  ──hooks──▶ the end-of-run summary (3.15t GIL warning)

READ FIRST
    docs/python/pytest-basics.md, docs/spec/09-testing-strategy.md §8

MILESTONE
    Introduced in K0.
"""

import importlib.util
import shutil
import sys
import sysconfig
from pathlib import Path

import pytest


def _is_free_threaded_build() -> bool:
    """Return True on a free-threaded ("t") interpreter build such as 3.15t.

    Returns:
        Whether the interpreter was built without the GIL.
    """
    return bool(sysconfig.get_config_var("Py_GIL_DISABLED"))


def _gil_enabled() -> bool:
    """Return True if the GIL is currently on (always True on normal builds).

    Returns:
        The current GIL state.
    """
    probe = getattr(sys, "_is_gil_enabled", None)
    return True if probe is None else bool(probe())


def _skip_reason(marker_name: str) -> str | None:
    """Explain why a test with this marker can't run here, or return None if it can.

    Why:
        One table of "marker → condition → reason" keeps every skip message consistent and
        tells the reader exactly what's missing.

    Args:
        marker_name: such as "requires_315".

    Returns:
        A human-readable skip reason, or None when the requirement is met.
    """
    running = sys.version.split()[0]
    if marker_name == "requires_314" and sys.version_info < (3, 14):
        return f"needs Python >= 3.14 (running {running})"
    if marker_name == "requires_315" and sys.version_info < (3, 15):
        return f"needs Python >= 3.15 (running {running})"
    if marker_name == "requires_ft" and not (_is_free_threaded_build() and not _gil_enabled()):
        return "needs a free-threaded build with the GIL off (for example 3.15t)"
    if marker_name == "requires_gcc" and shutil.which("cc") is None:
        return "needs a C compiler: sudo apt install build-essential"
    cgroup_root = Path("/sys/fs/cgroup/cgroup.controllers")
    if marker_name == "requires_cgroup" and not cgroup_root.exists():
        return "needs cgroup v2 (docs/spec/07-environment.md §6)"
    if marker_name == "requires_native" and importlib.util.find_spec("kiln_native") is None:
        return "needs the kiln_native extension built for this interpreter (K8)"
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Add a skip marker (with its reason) to every test whose requirements aren't met.

    Why:
        A pytest hook: pytest calls this function by name after collecting tests. Doing the
        skipping here means test files only *declare* needs; they never test the machine.

    Args:
        config: pytest's configuration (unused, but part of the hook's signature).
        items: every collected test.
    """
    for item in items:
        for marker in item.iter_markers():
            if marker.name.startswith("requires_"):
                reason = _skip_reason(marker.name)
                if reason is not None:
                    item.add_marker(pytest.mark.skip(reason=reason))


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """On a free-threaded build, warn loudly if the GIL got switched back on.

    Why:
        spec/09 §8: results from a "free-threaded" run that actually had the GIL would be
        misleading. An imported C extension without free-threading support turns the GIL
        back on (K4 explains which import did it).

    Args:
        terminalreporter: pytest's object for writing to the end-of-run summary.
    """
    if _is_free_threaded_build() and _gil_enabled():
        terminalreporter.write_line(
            "WARNING: free-threaded build, but the GIL is ENABLED: an imported extension "
            "re-enabled it. Free-threading results from this run are not trustworthy.",
            red=True,
        )
