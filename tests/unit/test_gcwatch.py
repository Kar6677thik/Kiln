"""Tests for the "who re-enabled the GC?" detector (K3 spec §5.7, step 3.4).

Each test runs with the GC disabled and restores the interpreter's state afterwards, because
the GC switch, gc.enable, sys.meta_path and gc.callbacks are process-wide.
"""

import ctypes
import gc
import importlib
import importlib.util
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from kiln.memory.gcwatch import GcEvent, GcWatch

pytestmark = pytest.mark.unit

PYTHON_FLIP = "import gc\ngc.enable()\n"
CAPI_FLIP = "import ctypes\nf = ctypes.pythonapi.PyGC_Enable\nf.restype = ctypes.c_int\nf()\n"


@pytest.fixture
def watched(tmp_path: Path) -> Iterator[tuple[GcWatch, list[GcEvent], Path]]:
    """A watch installed with the GC disabled; modules can be written into the yielded folder.

    Yields:
        (the watch, the list its events go to, a folder on sys.path).
    """
    events: list[GcEvent] = []
    was_enabled = gc.isenabled()
    original_enable = gc.enable
    sys.path.insert(0, str(tmp_path))
    gc.disable()
    watch = GcWatch(expect_disabled=True, on_event=events.append)
    watch.install()
    try:
        yield watch, events, tmp_path
    finally:
        watch.uninstall()
        sys.path.remove(str(tmp_path))
        for name in [n for n in sys.modules if n.startswith("gcw_")]:
            del sys.modules[name]
        assert gc.enable is original_enable
        if was_enabled:
            gc.enable()
        else:
            gc.disable()


def _module(folder: Path, name: str, source: str) -> None:
    """Write a module file and forget any cached bytecode or import state.

    Args:
        folder: where to write.
        name: the module name.
        source: its code.
    """
    (folder / f"{name}.py").write_text(source)
    importlib.invalidate_caches()


def test_shim_catches_a_python_call_with_the_exact_stack(watched) -> None:  # type: ignore[no-untyped-def]
    """Layer A: `gc.enable()` in Python is reported with a stack pointing at the caller."""
    _watch, events, folder = watched
    _module(folder, "gcw_python", PYTHON_FLIP)
    importlib.import_module("gcw_python")
    shim = [e for e in events if e.layer == "shim"]
    assert len(shim) == 1
    assert any("gcw_python.py:2" in frame for frame in shim[0].stack)


def test_import_flip_names_the_module_for_python_and_capi(watched) -> None:  # type: ignore[no-untyped-def]
    """Layer B: a flip during an import is blamed on that module, even from the C-API."""
    _watch, events, folder = watched
    _module(folder, "gcw_capi", CAPI_FLIP)
    importlib.import_module("gcw_capi")
    assert [e.layer for e in events] == ["import_flip"]  # no shim event: C bypasses it
    assert events[0].module == "gcw_capi"


def test_nested_import_blames_the_innermost_module(watched) -> None:  # type: ignore[no-untyped-def]
    """Layer B: outer imports inner, inner flips the GC → only `inner` is blamed."""
    _watch, events, folder = watched
    _module(folder, "gcw_inner", CAPI_FLIP)
    _module(folder, "gcw_outer", "import gcw_inner\nX = 1\n")
    importlib.import_module("gcw_outer")
    flips = [e.module for e in events if e.layer == "import_flip"]
    assert flips == ["gcw_inner"]


def test_sentinel_flags_an_automatic_collection_after_a_c_enable(watched) -> None:  # type: ignore[no-untyped-def]
    """Layer C(2): after a C-level enable, allocation triggers a collection, and it's flagged."""
    _watch, events, _folder = watched
    enable = ctypes.pythonapi.PyGC_Enable
    enable.restype = ctypes.c_int
    enable()
    garbage = [[i] for i in range(50_000)]  # allocations push the gen-0 counter over its limit
    del garbage
    assert any(e.layer == "collection_while_disabled" for e in events)


def test_sentinel_ignores_an_explicit_collect_while_disabled(watched) -> None:  # type: ignore[no-untyped-def]
    """An explicit gc.collect() while the GC is off is expected (lab tasks do it): no event."""
    _watch, events, _folder = watched
    gc.collect()
    assert events == []


def test_poll_reports_a_flip_once(watched) -> None:  # type: ignore[no-untyped-def]
    """Layer C(1): the poll reports the GC being on once, not on every tick."""
    watch, events, _folder = watched
    assert watch.poll() is None
    ctypes.pythonapi.PyGC_Enable()
    first = watch.poll()
    assert first is not None
    assert first.layer == "poll"
    assert watch.poll() is None
    assert [e.layer for e in events].count("poll") == 1


def test_uninstall_restores_everything() -> None:
    """After uninstall: the real gc.enable, no extra finder, no extra callback."""
    original = gc.enable
    finders = list(sys.meta_path)
    callbacks = list(gc.callbacks)
    watch = GcWatch(expect_disabled=True, on_event=lambda event: None)
    watch.install()
    watch.install()  # idempotent
    assert gc.enable is not original
    watch.uninstall()
    assert gc.enable is original
    assert sys.meta_path == finders
    assert gc.callbacks == callbacks


def test_imports_still_work_with_the_watch_installed(watched, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """Namespace packages, a C extension module and a zip import still load (§12 risk table)."""
    _watch, events, folder = watched
    (folder / "gcw_ns" / "sub").mkdir(parents=True)  # no __init__.py: a namespace package
    (folder / "gcw_ns" / "sub" / "leaf.py").write_text("VALUE = 42\n")
    archive = folder / "gcw_zipped.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("gcw_inzip.py", "VALUE = 'zip'\n")
    sys.path.insert(0, str(archive))
    try:
        importlib.invalidate_caches()
        assert importlib.import_module("gcw_ns.sub.leaf").VALUE == 42
        assert importlib.import_module("gcw_inzip").VALUE == "zip"
        sys.modules.pop("_json", None)
        assert importlib.import_module("_json") is not None
    finally:
        sys.path.remove(str(archive))
    assert events == []


def test_gcwatch_loads_by_file_path_without_kiln() -> None:
    """K7 constraint: the module works when loaded straight from its file."""
    path = Path(__file__).resolve().parents[2] / "src" / "kiln" / "memory" / "gcwatch.py"
    spec = importlib.util.spec_from_file_location("standalone_gcwatch", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["standalone_gcwatch"] = module  # WHY: dataclasses look the module up by name
    try:
        spec.loader.exec_module(module)
        assert hasattr(module, "GcWatch")
    finally:
        del sys.modules["standalone_gcwatch"]
