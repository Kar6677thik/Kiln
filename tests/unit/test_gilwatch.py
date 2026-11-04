"""gilwatch's import-chain logic, tested on every build with a simulated GIL switch.

The real flip needs a free-threaded build and a C extension without `Py_mod_gil`
(tests/integration/test_gil_reenabler.py, which needs a C compiler). Here a fake
`sys._is_gil_enabled` lets a plain Python module "flip the GIL" by importing it, so the
finder, the innermost-culprit rule and the chain are checked deterministically.
"""

import importlib
import sys
import sysconfig
from collections.abc import Iterator
from pathlib import Path

import pytest

from kiln.worker import gilwatch

pytestmark = pytest.mark.unit

FLIP = "import sys\nsys._kiln_fake_gil[0] = True\n"


@pytest.fixture
def watched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[gilwatch.GilWatch, Path]]:
    """A GilWatch installed on a pretend free-threaded build whose GIL starts off.

    Yields:
        (the watch, a folder on sys.path to write modules into).
    """
    state = [False]
    monkeypatch.setattr(sys, "_kiln_fake_gil", state, raising=False)
    monkeypatch.setattr(sys, "_is_gil_enabled", lambda: state[0], raising=False)
    real = sysconfig.get_config_var
    monkeypatch.setattr(
        sysconfig, "get_config_var", lambda n: 1 if n == "Py_GIL_DISABLED" else real(n)
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    watch = gilwatch.GilWatch(on_event=lambda event: None)
    assert watch.install()
    try:
        yield watch, tmp_path
    finally:
        watch.uninstall()
        for name in [n for n in sys.modules if n.startswith("gw_")]:
            del sys.modules[name]


def _write(folder: Path, name: str, source: str) -> None:
    """Write a module and refresh the import caches.

    Args:
        folder: where.
        name: module name (dots make packages).
        source: its code.
    """
    parts = name.split(".")
    for i in range(1, len(parts)):
        package = folder.joinpath(*parts[:i])
        package.mkdir(exist_ok=True)
        (package / "__init__.py").touch()
    folder.joinpath(*parts[:-1], parts[-1] + ".py").write_text(source)
    importlib.invalidate_caches()


def test_a_module_that_keeps_the_gil_off_is_not_reported(watched) -> None:  # type: ignore[no-untyped-def]
    """Like kiln_gil_labs.without_gil: importing it changes nothing, so nothing is reported."""
    watch, folder = watched
    _write(folder, "gw_safe", "X = 1\n")
    importlib.import_module("gw_safe")
    assert watch.events == []


def test_the_innermost_culprit_is_named_with_its_chain(watched) -> None:  # type: ignore[no-untyped-def]
    """App → helpers → ext, and ext flips the GIL: ext is blamed, chain app>helpers>ext."""
    watch, folder = watched
    _write(folder, "gw_ext", FLIP)
    _write(folder, "gw_helpers", "import gw_ext\n")
    _write(folder, "gw_app", "import gw_helpers\n")
    importlib.import_module("gw_app")
    assert [(e.module, e.import_chain) for e in watch.events] == [
        ("gw_ext", ("gw_app", "gw_helpers", "gw_ext"))
    ]
    assert watch.events[0].origin.endswith("gw_ext.py")


def test_only_the_first_flip_is_reported(watched) -> None:  # type: ignore[no-untyped-def]
    """Once the GIL is on, later imports can't flip it again: one report, not two."""
    watch, folder = watched
    _write(folder, "gw_first", FLIP)
    _write(folder, "gw_second", FLIP)
    importlib.import_module("gw_first")
    importlib.import_module("gw_second")
    assert [e.module for e in watch.events] == ["gw_first"]


def test_not_installed_on_a_gil_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """On a normal build the watch refuses to install: there is no GIL to re-enable."""
    real = sysconfig.get_config_var
    monkeypatch.setattr(
        sysconfig, "get_config_var", lambda n: 0 if n == "Py_GIL_DISABLED" else real(n)
    )
    finders = list(sys.meta_path)
    assert gilwatch.GilWatch(on_event=lambda event: None).install() is False
    assert sys.meta_path == finders
