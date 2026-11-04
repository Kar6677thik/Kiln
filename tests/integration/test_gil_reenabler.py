"""gilwatch against the real C extensions on a free-threaded build (K4 AC-4.4).

Needs: a free-threaded interpreter (3.15t) and the kiln-gil-labs package, which needs a C
compiler to build (`sudo apt install build-essential`, then sync --group lab-k4). Skipped,
with that reason, everywhere else.
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.requires_ft, pytest.mark.requires_gcc]
DEMO = Path(__file__).resolve().parents[2] / "labs" / "k4" / "gil_demo.py"


def probe(module: str, env_extra: dict[str, str] | None = None) -> dict[str, object]:
    """Run the gil_demo probe for one module in a fresh interpreter.

    Args:
        module: what to import.
        env_extra: extra environment variables.

    Returns:
        The probe's report.
    """
    pytest.importorskip("kiln_gil_labs", reason="kiln-gil-labs isn't built (needs a C compiler)")
    spec_globals: dict[str, object] = {}
    exec(compile(DEMO.read_text(), str(DEMO), "exec"), spec_globals)  # noqa: S102  # WHY: reuse the demo's probe
    report = spec_globals["probe"](module, env_extra or {})  # type: ignore[operator]
    assert isinstance(report, dict)
    return report


def test_without_gil_keeps_the_gil_off_and_reports_nothing() -> None:
    """kiln_gil_labs.without_gil declares Py_MOD_GIL_NOT_USED: GIL stays off, no report."""
    report = probe("kiln_gil_labs.without_gil")
    assert (report["before"], report["after"], report["reports"]) == (False, False, [])


def test_with_gil_through_a_wrapper_is_named_with_its_chain() -> None:
    """wrapper_pkg imports kiln_gil_labs.with_gil: the GIL turns on, chain wrapper_pkg>...>with_gil."""
    report = probe("wrapper_pkg")
    assert report["after"] is True
    reports = report["reports"]
    assert isinstance(reports, list)
    assert reports[0][0] == "kiln_gil_labs.with_gil"
    assert reports[0][1].startswith("wrapper_pkg>")
    assert reports[0][1].endswith("kiln_gil_labs.with_gil")


def test_python_gil_0_keeps_it_off() -> None:
    """Gate G4.9: with PYTHON_GIL=0 the GIL stays off even after the legacy import."""
    report = probe("kiln_gil_labs.with_gil", {"PYTHON_GIL": "0"})
    assert report["after"] is False
