"""The `kiln mem` table: RSS, PSS, USS and friends for a master and its workers.

WHY THIS MODULE EXISTS
    "How much memory does Kiln use?" has a trap in it: adding up RSS counts every shared page
    once per process, so four forked workers sharing 300 MiB look like 1.2 GiB. PSS divides
    each shared page between its sharers, so Σ PSS is the real total. This module gathers the
    numbers from /proc/<pid>/smaps_rollup and prints them side by side, with the Σ row that
    makes the trap visible (docs/concepts/smaps.md, docs/concepts/memory-metrics.md).

HOW IT FITS
    cli.py `kiln mem PID... [--children-of PID] [--json] [--watch S]`
        ──collect()──▶ procfs.read_smaps_rollup(pid) for each process ──▶ render_text / to_json

READ FIRST
    docs/concepts/smaps.md, docs/concepts/memory-metrics.md

MILESTONE
    Introduced in K3.
"""

from dataclasses import dataclass
from pathlib import Path

from kiln.memory.procfs import PROC, ProcessGone, SmapsRollup, children_of, read_smaps_rollup

MIB = 1024 * 1024
COLUMNS = ("pid", "role", "RSS", "PSS", "USS", "Shared", "Private_Dirty", "Swap")
FOOTNOTE = (
    "Σ RSS counts shared pages once per process, so it can exceed the memory really used; "
    "Σ PSS is the real total (shared pages divided between their sharers)."
)
HELP_COLUMNS = """columns (MiB):
  RSS            every page of the process in RAM, shared ones counted in full
  PSS            RSS with each shared page divided by its number of sharers; Σ PSS = real total
  USS            pages only this process uses: what you'd get back if it exited
  Shared         pages shared with at least one other process (clean + dirty)
  Private_Dirty  pages only this process has written: copy-on-write copies land here
  Swap           pages pushed out to disk (should be 0)"""


@dataclass(frozen=True, slots=True)
class Row:
    """One process's line in the table.

    Why:
        Keeps the pid, a human role and the numbers together for both output formats.
    """

    pid: int
    role: str
    rollup: SmapsRollup


def role_of(pid: int, proc_root: Path = PROC) -> str:
    """Guess a Kiln role from the process's command line.

    Why:
        A forked worker has the *same* command line as its master (fork copies argv), so
        this is only a hint; collect() overrides it with what the process tree says.

    Args:
        pid: the process.
        proc_root: where /proc is.

    Returns:
        "forkserver", "worker" (spawned), "kiln" or the start of the command line.
    """
    try:
        raw = (proc_root / str(pid) / "cmdline").read_bytes()
    except OSError:
        return "?"
    # WHY: the arguments in cmdline are separated by NUL bytes ("\0").
    command = raw.replace(b"\0", b" ").decode(errors="replace").strip()
    if "kiln.launch.forkserver_main" in command:
        return "forkserver"
    if "kiln.worker" in command:
        return "worker"
    if "kiln" in command:
        return "kiln"
    return command[:30] or "?"


def collect(pids: list[int], children_of_pid: int | None, proc_root: Path = PROC) -> list[Row]:
    """Read smaps_rollup for the given processes (and a master's whole tree).

    Why:
        With --children-of, the tree tells roles apart: the root is the master, a child is a
        worker or the forkserver, and the forkserver's children are workers.

    Args:
        pids: explicit pids.
        children_of_pid: a master pid whose children (and forkserver grandchildren) to add.
        proc_root: where /proc is.

    Returns:
        One Row per process that still exists.
    """
    wanted: list[tuple[int, str]] = [(pid, role_of(pid, proc_root)) for pid in pids]
    if children_of_pid is not None:
        wanted.append((children_of_pid, "master"))
        for child in children_of(children_of_pid, proc_root):
            role = role_of(child, proc_root)
            if role == "forkserver":
                wanted.append((child, "forkserver"))
                wanted.extend((g, "worker") for g in children_of(child, proc_root))
            else:
                wanted.append((child, "worker"))
    rows = []
    for pid, role in wanted:
        try:
            rows.append(Row(pid, role, read_smaps_rollup(pid, proc_root)))
        except ProcessGone:
            continue  # WHY: a worker can exit between listing and reading
    return rows


def _values(rollup: SmapsRollup) -> tuple[int, int, int, int, int, int]:
    """The numeric columns in display order.

    Why:
        Text and JSON output must list the same numbers in the same order.

    Args:
        rollup: one process's totals.

    Returns:
        RSS, PSS, USS, Shared, Private_Dirty, Swap in bytes.
    """
    return (
        rollup.rss,
        rollup.pss,
        rollup.uss,
        rollup.shared,
        rollup.private_dirty,
        rollup.swap,
    )


def render_text(rows: list[Row]) -> str:
    """Format the table in MiB with one decimal, plus the Σ row and the footnote.

    Why:
        Seeing Σ RSS next to Σ PSS is the whole lesson of the table.

    Args:
        rows: the rows.

    Returns:
        The text.
    """
    lines = ["".join(f"{c:>14}" if i > 1 else f"{c:<12}" for i, c in enumerate(COLUMNS))]
    totals = [0] * 6
    for row in rows:
        values = _values(row.rollup)
        totals = [t + v for t, v in zip(totals, values, strict=True)]
        cells = "".join(f"{v / MIB:>14.1f}" for v in values)
        lines.append(f"{row.pid:<12}{row.role:<12}{cells}")
    cells = "".join(f"{v / MIB:>14.1f}" for v in totals)
    lines.append(f"{'Σ':<12}{f'{len(rows)} procs':<12}{cells}")
    lines.append("")
    lines.append(FOOTNOTE)
    return "\n".join(lines)


def to_json(rows: list[Row]) -> list[dict[str, object]]:
    """The rows as JSON-ready dicts, in bytes.

    Why:
        Scripts and labs want exact integers, not rounded MiB.

    Args:
        rows: the rows.

    Returns:
        One dict per row.
    """
    out: list[dict[str, object]] = []
    for row in rows:
        rss, pss, uss, shared, private_dirty, swap = _values(row.rollup)
        out.append(
            {
                "pid": row.pid,
                "role": row.role,
                "rss": rss,
                "pss": pss,
                "uss": uss,
                "shared": shared,
                "private_dirty": private_dirty,
                "swap": swap,
            }
        )
    return out
