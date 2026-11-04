"""smaps_rollup on real processes: copy-on-write shows up as Private_Dirty (K3 step 3.1, AC-3.1)."""

import json
import os
import subprocess
import sys
from collections.abc import Callable

import pytest
from kiln_helpers import KilnServer, wait_for

from kiln.memory.procfs import read_smaps_rollup

pytestmark = pytest.mark.integration
MIB = 1024 * 1024
Start = Callable[..., KilnServer]


def test_writing_shared_pages_in_a_child_grows_its_private_dirty() -> None:
    """A forked child that writes N MiB of inherited memory gains ≥ N MiB of Private_Dirty.

    This is copy-on-write made visible: before the writes the pages are shared with the
    parent; each write makes the kernel copy one 4 KiB page into the child.
    """
    size = 64 * MIB
    buffer = bytearray(os.urandom(size))  # touched in the parent, so the pages are resident
    read_end, write_end = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover  (the child)
        os.close(read_end)
        before = read_smaps_rollup("self").private_dirty
        for offset in range(0, size, 4096):
            buffer[offset] = 1  # one byte per page is enough to copy the whole page
        after = read_smaps_rollup("self").private_dirty
        os.write(write_end, json.dumps([before, after]).encode())
        os._exit(0)
    os.close(write_end)
    with os.fdopen(read_end) as reader:
        before, after = json.loads(reader.read())
    os.waitpid(pid, 0)
    assert after - before >= size * 0.95


def test_kiln_mem_shows_master_and_workers(kiln_server: Start) -> None:
    """`kiln mem --children-of <master> --json`: master + workers, and Σ PSS ≤ Σ RSS."""
    server = kiln_server("--workers", "2")
    wait_for(lambda: len(server.worker_pids()) == 2, what="2 workers")
    result = subprocess.run(
        [sys.executable, "-m", "kiln", "mem", "--children-of", str(server.proc.pid), "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    rows = json.loads(result.stdout)
    roles = sorted(row["role"] for row in rows)
    assert roles.count("master") == 1
    assert roles.count("worker") == 2
    assert sum(row["pss"] for row in rows) <= sum(row["rss"] for row in rows)
    text = subprocess.run(
        [sys.executable, "-m", "kiln", "mem", "--children-of", str(server.proc.pid)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "Σ PSS is the real total" in text
