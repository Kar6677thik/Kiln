"""Tests for the smaps_rollup reader and children_of (K3 spec §5.1), on fixture /proc trees."""

from pathlib import Path

import pytest

from kiln.memory.procfs import ProcessGone, children_of, parse_smaps_rollup, read_smaps_rollup

pytestmark = pytest.mark.unit

# A real smaps_rollup layout (values made up for the test, in kB as the kernel writes them).
ROLLUP = """\
00400000-7ffd1a7f2000 ---p 00000000 00:00 0                              [rollup]
Rss:              120000 kB
Pss:               40000 kB
Pss_Dirty:         30000 kB
Pss_Anon:          35000 kB
Pss_File:           5000 kB
Pss_Shmem:             0 kB
Shared_Clean:       6000 kB
Shared_Dirty:     100000 kB
Private_Clean:       100 kB
Private_Dirty:     13900 kB
Referenced:       120000 kB
Anonymous:        114000 kB
Swap:                  0 kB
SwapPss:               0 kB
Locked:                0 kB
"""


def test_values_are_converted_from_kib_to_bytes() -> None:
    """Every field is the kernel's "kB" (KiB) value × 1024."""
    rollup = parse_smaps_rollup(ROLLUP)
    assert rollup.rss == 120000 * 1024
    assert rollup.pss == 40000 * 1024
    assert rollup.pss_anon == 35000 * 1024
    assert rollup.private_dirty == 13900 * 1024


def test_uss_and_shared_are_sums_of_their_parts() -> None:
    """USS = private clean + dirty; shared = shared clean + dirty (spec/10 §7)."""
    rollup = parse_smaps_rollup(ROLLUP)
    assert rollup.uss == (100 + 13900) * 1024
    assert rollup.shared == (6000 + 100000) * 1024


def test_missing_lines_become_zero() -> None:
    """Older kernels lack Pss_Anon/Pss_File/Pss_Shmem: those fields are 0, not an error."""
    rollup = parse_smaps_rollup("Rss: 8 kB\nPss: 4 kB\n")
    assert (rollup.rss, rollup.pss, rollup.pss_anon, rollup.swap) == (8192, 4096, 0, 0)


def test_reading_a_vanished_process_raises_process_gone(tmp_path: Path) -> None:
    """A pid with no /proc folder raises ProcessGone (an OSError subclass)."""
    with pytest.raises(ProcessGone):
        read_smaps_rollup(424242, proc_root=tmp_path)


def test_read_smaps_rollup_reads_the_file(tmp_path: Path) -> None:
    """read_smaps_rollup parses /proc/<pid>/smaps_rollup under the given root."""
    (tmp_path / "7").mkdir()
    (tmp_path / "7" / "smaps_rollup").write_text(ROLLUP)
    assert read_smaps_rollup(7, proc_root=tmp_path).pss == 40000 * 1024


def test_read_self_works_on_this_machine() -> None:
    """The real /proc/self/smaps_rollup is readable and RSS ≥ PSS ≥ USS > 0."""
    rollup = read_smaps_rollup("self")
    assert rollup.rss >= rollup.pss >= rollup.uss > 0


def test_children_of_uses_the_task_children_files(tmp_path: Path) -> None:
    """Children are read from every thread's task/<tid>/children file."""
    for tid, kids in (("10", "11 12"), ("13", "14")):
        folder = tmp_path / "10" / "task" / tid
        folder.mkdir(parents=True)
        (folder / "children").write_text(kids)
    assert children_of(10, proc_root=tmp_path) == [11, 12, 14]


def test_children_of_falls_back_to_scanning_stat(tmp_path: Path) -> None:
    """Without children files, the parent pid in each /proc/<n>/stat is used.

    The command name may contain spaces and parentheses, so parsing splits after the last ")".
    """
    for pid, ppid in ((21, 20), (22, 20), (23, 1)):
        (tmp_path / str(pid)).mkdir()
        (tmp_path / str(pid) / "stat").write_text(f"{pid} (my (odd) name) S {ppid} 1 1 0")
    assert children_of(20, proc_root=tmp_path) == [21, 22]
