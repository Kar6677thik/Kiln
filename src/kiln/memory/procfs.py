"""Read a process's memory numbers from /proc/<pid>/status and /proc/<pid>/smaps_rollup.

WHY THIS MODULE EXISTS
    "How big is this worker?" decides when a worker gets recycled (spec/04 §5.5), and it's
    the first measurement of every memory lab. Linux answers in /proc/<pid>/status, a text
    file the kernel generates when you read it. This module turns its lines into bytes.

HOW IT FITS
    worker/heartbeat.py ──read_status("self")──▶ StatusInfo.vm_rss ──▶ heartbeat.rss_bytes
    worker/main.py (max_rss check) ──read_status("self")──▶ retire if too big
    master/supervisor.py, kiln mem, labs/k3 ──read_smaps_rollup(pid)──▶ PSS / USS / shared

READ FIRST
    docs/concepts/memory-metrics.md, docs/concepts/smaps.md,
    docs/concepts/files-and-file-descriptors.md

MILESTONE
    Introduced in K1 (status). K3 adds smaps_rollup (PSS/USS/shared).
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PROC = Path("/proc")
log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StatusInfo:
    """The memory lines of /proc/<pid>/status, converted to bytes.

    Why:
        Named fields instead of a dict of strings: `info.vm_rss` can't be misspelled silently.
    """

    vm_rss: int  # resident memory now (includes shared pages: see memory-metrics.md)
    vm_hwm: int  # "high water mark": the largest VmRSS this process ever had
    rss_anon: int  # resident memory not backed by a file (heap, stacks)
    rss_file: int  # resident memory backed by files (code, mapped libraries)
    rss_shmem: int  # resident shared memory


def parse_status(text: str) -> StatusInfo:
    r"""Parse the text of a /proc/<pid>/status file.

    Why:
        Separate from the file reading so it can be tested with any text.

    Args:
        text: the file contents.

    Returns:
        The memory fields in bytes (0 for any line that is missing).

    Example:
        >>> parse_status("VmHWM:\t  2048 kB\nVmRSS:\t  1024 kB\n").vm_rss
        1048576
    """
    found: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        # WHY: memory lines look like "VmRSS:     41234 kB". The kernel's "kB" means KiB
        # (1024 bytes), so we multiply by 1024.
        if len(parts) == 2 and parts[1] == "kB" and parts[0].isdigit():
            found[name] = int(parts[0]) * 1024
    return StatusInfo(
        vm_rss=found.get("VmRSS", 0),
        vm_hwm=found.get("VmHWM", 0),
        rss_anon=found.get("RssAnon", 0),
        rss_file=found.get("RssFile", 0),
        rss_shmem=found.get("RssShmem", 0),
    )


def read_status(pid: int | Literal["self"], proc_root: Path = PROC) -> StatusInfo:
    """Read /proc/<pid>/status for a process ("self" means the calling process).

    Args:
        pid: a process id, or "self".
        proc_root: where /proc is (tests pass a fake folder).

    Returns:
        The parsed memory fields.

    Raises:
        OSError: if the process doesn't exist (FileNotFoundError) or can't be read.
    """
    # CONCEPT(files-and-file-descriptors): there is no file on disk; the kernel builds this
    # text at the moment we read it.
    return parse_status((proc_root / str(pid) / "status").read_text(encoding="utf-8"))


class ProcessGone(OSError):
    """The process exited (its /proc folder vanished) while we were reading it.

    Why:
        Workers die and get replaced all the time; callers want to tell "it's gone" apart from
        a real read error such as a permission problem.
    """


@dataclass(frozen=True, slots=True)
class SmapsRollup:
    """The totals of /proc/<pid>/smaps_rollup, converted to bytes (spec/10 §7).

    Why:
        RSS counts pages shared with other processes in full, so it can't answer "how much
        memory does this worker really cost?". smaps_rollup splits memory into shared/private
        and clean/dirty, and gives PSS, which divides each shared page between its sharers
        (docs/concepts/smaps.md).
    """

    rss: int  # every resident page, shared ones included
    pss: int  # shared pages divided by the number of sharers: Σ PSS = real total
    pss_anon: int  # the part of PSS not backed by a file (heap: Python objects live here)
    pss_file: int  # the part of PSS backed by files (the interpreter, .so libraries)
    pss_shmem: int  # the part of PSS in shared memory segments
    shared_clean: int  # shared with another process, never written since
    shared_dirty: int  # shared, but written before it was shared
    private_clean: int  # only ours, unmodified
    private_dirty: int  # only ours, written: copy-on-write copies show up HERE
    swap: int  # pushed out to disk (should be 0 in every lab)
    swap_pss: int  # swap, divided between sharers like PSS

    @property
    def uss(self) -> int:
        """Unique Set Size: the memory freed if this process exited.

        Returns:
            private_clean + private_dirty, in bytes.
        """
        return self.private_clean + self.private_dirty

    @property
    def shared(self) -> int:
        """Pages this process shares with at least one other process.

        Returns:
            shared_clean + shared_dirty, in bytes.
        """
        return self.shared_clean + self.shared_dirty


# The smaps_rollup line names, in the order of the SmapsRollup fields.
_SMAPS_FIELDS = {
    "Rss": "rss",
    "Pss": "pss",
    "Pss_Anon": "pss_anon",
    "Pss_File": "pss_file",
    "Pss_Shmem": "pss_shmem",
    "Shared_Clean": "shared_clean",
    "Shared_Dirty": "shared_dirty",
    "Private_Clean": "private_clean",
    "Private_Dirty": "private_dirty",
    "Swap": "swap",
    "SwapPss": "swap_pss",
}


def parse_smaps_rollup(text: str) -> SmapsRollup:
    r"""Parse the text of a /proc/<pid>/smaps_rollup file.

    Why:
        Separate from the reading, so tests can feed fixture text; missing lines (older
        kernels lack Pss_Anon etc.) become 0 and are logged at DEBUG.

    Args:
        text: the file contents.

    Returns:
        The totals in bytes.

    Example:
        >>> parse_smaps_rollup("Rss:   8 kB\nPss:   4 kB\n").pss
        4096
    """
    found: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        # GOTCHA: the kernel writes "kB" but means KiB (1024 bytes), exactly like /status.
        if name in _SMAPS_FIELDS and len(parts) == 2 and parts[1] == "kB":
            found[_SMAPS_FIELDS[name]] = int(parts[0]) * 1024
    for line_name, field_name in _SMAPS_FIELDS.items():
        if field_name not in found:
            log.debug("smaps_rollup has no %s line; using 0", line_name)
    # PY: ** unpacks a dict into keyword arguments: SmapsRollup(rss=..., pss=..., ...).
    return SmapsRollup(**{field: found.get(field, 0) for field in _SMAPS_FIELDS.values()})


def read_smaps_rollup(pid: int | Literal["self"], proc_root: Path = PROC) -> SmapsRollup:
    """Read /proc/<pid>/smaps_rollup.

    Why:
        The one memory reading that tells shared from private pages. It is slower than
        /status (the kernel walks every memory mapping to produce it), so the master reads it
        at most every 5 s per worker (ADR-006).

    Args:
        pid: a process id, or "self".
        proc_root: where /proc is (tests pass a fake folder).

    Returns:
        The parsed totals.

    Raises:
        ProcessGone: the process doesn't exist (any more).
        OSError: any other read error (for example permissions).
    """
    try:
        text = (proc_root / str(pid) / "smaps_rollup").read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError) as error:
        raise ProcessGone(f"process {pid} is gone") from error
    return parse_smaps_rollup(text)


def children_of(pid: int, proc_root: Path = PROC) -> list[int]:
    """List the direct children of a process.

    Why:
        `kiln mem --children-of <master>` and the K3 sampler need "the master's workers"
        without asking the master. The kernel lists each thread's children in
        /proc/<pid>/task/<tid>/children; if that file is missing (kernels built without
        CONFIG_PROC_CHILDREN) we scan every /proc/<n>/stat for its parent pid.

    Args:
        pid: the parent.
        proc_root: where /proc is.

    Returns:
        The child pids, sorted.
    """
    children: set[int] = set()
    task_dir = proc_root / str(pid) / "task"
    files = list(task_dir.glob("*/children")) if task_dir.is_dir() else []
    if files:
        for path in files:
            try:
                children.update(int(word) for word in path.read_text().split())
            except OSError:
                continue  # WHY: the thread ended while we were reading
        return sorted(children)
    for stat in proc_root.glob("[0-9]*/stat"):
        try:
            # WHY: the command name (field 2) may contain spaces and ")", so split after the
            # LAST ")": what follows is "state ppid ...".
            fields = stat.read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if int(fields[1]) == pid:
            children.add(int(stat.parent.name))
    return sorted(children)
