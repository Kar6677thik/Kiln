"""Direct tests of fork facts the launcher relies on (gates G1.3 and G1.7)."""

import atexit
import os
import random
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_random_differs_between_parent_and_child() -> None:
    """Gate G1.3: after os.fork(), the child's random numbers differ from the parent's.

    CPython reseeds `random` in the child through os.register_at_fork; without that, every
    forked worker would produce the same "random" jitter.
    """
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        os.write(write_fd, repr(random.random()).encode())  # noqa: S311
        os._exit(0)
    os.close(write_fd)
    child_value = os.read(read_fd, 100).decode()
    os.close(read_fd)
    os.waitpid(pid, 0)
    assert child_value != repr(random.random())  # noqa: S311


def test_child_does_not_run_parent_atexit(tmp_path: Path) -> None:
    """Gate G1.7: a child ending with os._exit skips handlers the parent registered (ADR-028)."""
    marker = tmp_path / "atexit-ran"

    def write_marker() -> None:
        """An atexit handler that would leave evidence if it ran."""
        marker.write_text("ran")

    atexit.register(write_marker)
    try:
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        assert not marker.exists()
    finally:
        atexit.unregister(write_marker)
