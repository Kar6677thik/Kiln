"""Read a process's memory numbers from /proc/<pid>/status.

WHY THIS MODULE EXISTS
    "How big is this worker?" decides when a worker gets recycled (spec/04 §5.5), and it's
    the first measurement of every memory lab. Linux answers in /proc/<pid>/status, a text
    file the kernel generates when you read it. This module turns its lines into bytes.

HOW IT FITS
    worker/heartbeat.py ──read_status("self")──▶ StatusInfo.vm_rss ──▶ heartbeat.rss_bytes
    worker/main.py (max_rss check) ──read_status("self")──▶ retire if too big

READ FIRST
    docs/concepts/memory-metrics.md, docs/concepts/files-and-file-descriptors.md

MILESTONE
    Introduced in K1 (status). K3 adds smaps_rollup (PSS/USS/shared).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PROC = Path("/proc")


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
