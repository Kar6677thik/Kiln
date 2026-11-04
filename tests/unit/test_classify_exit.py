"""Tests for death classification: every row of docs/spec/04-supervisor.md §4.1.

Wait statuses are built the way Linux encodes them: an exit code N is `N << 8`; death by
signal S is `S` (plus 0x80 if a core was dumped). See docs/concepts/exit-status.md.
"""

import signal
from pathlib import Path

import pytest

from kiln.master.supervisor import (
    backoff_delay,
    classify_exit,
    own_cgroup_events,
    read_oom_kill_count,
)
from kiln.master.workers import WorkerState


def exited(code: int) -> int:
    """Build the wait status for "exited with code N".

    Args:
        code: the exit code.

    Returns:
        The status integer.
    """
    return code << 8


@pytest.mark.parametrize(
    ("status", "state", "kill_sent", "how", "oom"),
    [
        (exited(0), WorkerState.RETIRING, None, "retired", False),
        (exited(0), WorkerState.READY, None, "exited_unexpectedly", False),
        (exited(3), WorkerState.BOOTING, None, "boot_failed", False),
        (exited(1), WorkerState.READY, None, "crashed", False),
        (signal.SIGKILL, WorkerState.READY, signal.SIGKILL, "killed_by_master", False),
        (signal.SIGKILL, WorkerState.READY, None, "killed", True),
        (signal.SIGSEGV, WorkerState.READY, None, "killed", False),
        (signal.SIGABRT | 0x80, WorkerState.READY, None, "killed", False),
    ],
    ids=[
        "retired",
        "unexpected-exit",
        "boot-failed",
        "crashed",
        "killed-by-us",
        "external-sigkill",
        "segv",
        "abort-core",
    ],
)
def test_every_classification_row(
    status: int, state: WorkerState, kill_sent: signal.Signals | None, how: str, oom: bool
) -> None:
    """Each wait status maps to the classification spec/04 §4.1 prescribes.

    The key row: a SIGKILL that Kiln did NOT send is "suspected OOM" (the Back Market lesson).
    """
    death = classify_exit(status, state, kill_sent)
    assert death.how == how
    assert death.suspected_oom is oom


def test_signal_names_and_core_flag_are_reported() -> None:
    """The log needs the signal's name and whether a core was dumped."""
    death = classify_exit(signal.SIGABRT | 0x80, WorkerState.READY, None)
    assert death.signal_name == "SIGABRT"
    assert death.core_dumped


def test_backoff_doubles_up_to_thirty_seconds() -> None:
    """Crash-loop backoff is 0.1 × 2^(k−1) seconds, capped at 30 (spec/04 §5.1)."""
    assert [backoff_delay(k) for k in (0, 1, 2, 3)] == [0.0, 0.1, 0.2, 0.4]
    assert backoff_delay(20) == 30.0


def test_oom_counter_is_read_from_memory_events(tmp_path: Path) -> None:
    """The oom_kill line of memory.events is the OOM evidence (spec/04 §4.2)."""
    proc = tmp_path / "proc" / "self"
    proc.mkdir(parents=True)
    (proc / "cgroup").write_text("0::/kiln.scope\n")
    group = tmp_path / "cg" / "kiln.scope"
    group.mkdir(parents=True)
    (group / "memory.events").write_text("low 0\nhigh 0\nmax 3\noom 1\noom_kill 2\n")
    events = own_cgroup_events(tmp_path / "proc", tmp_path / "cg")
    assert events == group / "memory.events"
    assert read_oom_kill_count(events) == 2
    assert read_oom_kill_count(None) is None
