"""Unit tests for kiln.memory.gcmodes hooks that don't need a server."""

import gc

import pytest

from kiln.memory import gcmodes

pytestmark = pytest.mark.unit


def test_before_fork_freezes_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze mode calls gc.freeze() before the first fork only (ADR-036: 3.15t re-freeze bug).

    On the free-threaded build, freezing again writes every object header, dirtying pages
    already shared with the workers forked earlier.
    """
    calls: list[int] = []
    monkeypatch.setattr(gc, "freeze", lambda: calls.append(1))
    monkeypatch.setitem(gcmodes._FROZEN, "done", False)
    for _ in range(4):
        gcmodes.before_fork("freeze")
    assert calls == [1]


def test_before_fork_does_nothing_in_other_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only `freeze` freezes."""
    calls: list[int] = []
    monkeypatch.setattr(gc, "freeze", lambda: calls.append(1))
    monkeypatch.setitem(gcmodes._FROZEN, "done", False)
    for mode in ("default", "disabled", "threshold0"):
        gcmodes.before_fork(mode)
    assert calls == []
