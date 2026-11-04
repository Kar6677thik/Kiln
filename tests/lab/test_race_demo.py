"""The planted race (K4 §5.7) under spec/09 §8's special rule for race tests.

`locked` must be exact every time. `racy` must lose updates in at least one of 20 trials on
the free-threaded build; if it doesn't, the test is reported as xfail (never a failure, never
a pass), because a race not happening proves nothing. Full statistics come from the lab.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = [pytest.mark.lab, pytest.mark.requires_ft]
SCRIPT = Path(__file__).resolve().parents[2] / "labs" / "k4" / "race_demo.py"


def load_demo() -> ModuleType:
    """Import labs/k4/race_demo.py by path (labs/ isn't a package).

    Returns:
        The module.
    """
    spec = importlib.util.spec_from_file_location("race_demo", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["race_demo"] = module
    spec.loader.exec_module(module)
    return module


def test_locked_is_exact_in_every_trial() -> None:
    """20 trials × 8 threads × 50,000 records: never a lost update."""
    demo = load_demo()
    for _ in range(20):
        assert demo.trial("locked", 8, 50_000)[:2] == (0, 0)


def test_racy_loses_updates_on_the_free_threaded_build() -> None:
    """At least one of 20 trials loses updates; otherwise xfail (spec/09 §8)."""
    demo = load_demo()
    if not any(demo.trial("racy", 8, 50_000)[0] > 0 for _ in range(20)):
        pytest.xfail("race not reproduced on this run; see docs/labs/K4-concurrency.md")
