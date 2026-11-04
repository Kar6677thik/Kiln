"""worker.exit_via_os_exit: Instagram's shutdown trick, and where it applies (K3 §5.6, AC-3.5).

The app below leaves two marks when a worker shuts down: one from its atexit handler, and one
from the __del__ of a garbage cycle that only the interpreter's final collection can free.
"""

import signal
from collections.abc import Callable
from pathlib import Path

import pytest
from kiln_helpers import KilnServer

pytestmark = pytest.mark.integration
Start = Callable[..., KilnServer]

EXIT_APP = '''"""A test app that records whether atexit handlers and the final collection ran."""
import atexit
import os

import kiln

MARKS = os.environ["KILN_TEST_MARK_DIR"]


def _mark(kind, _join=os.path.join, _open=open, _getpid=os.getpid):
    # WHY: default arguments keep references alive during teardown, when globals may be None.
    with _open(_join(MARKS, f"{kind}-{_getpid()}"), "w"):
        pass


class Garbage:
    def __del__(self, _mark=_mark):
        _mark("final_gc")


def _leave_garbage():
    # WHY: an unreachable cycle created at exit can only be freed by the interpreter's final
    # collection (finalization), which os._exit skips. Module teardown is not a reliable
    # probe: Python doesn't promise __del__ for objects still alive at exit.
    cycle = Garbage()
    cycle.me = cycle
    del cycle
    _mark("atexit")


atexit.register(_leave_garbage)
app = kiln.App("exitapp")


@app.task
def whoami():
    return os.getpid()
'''


def run_and_stop(kiln_server: Start, tmp_path: Path, trick: bool) -> tuple[bool, bool]:
    """Start one worker, learn its pid, stop gracefully, and report which marks it left.

    Args:
        kiln_server: the fixture.
        tmp_path: a temporary folder.
        trick: worker.exit_via_os_exit.

    Returns:
        (atexit handler ran, final collection ran) for the worker.
    """
    (tmp_path / "exit_app.py").write_text(EXIT_APP)
    marks = tmp_path / "marks"
    marks.mkdir()
    env = {
        "KILN_APP__PYTHONPATH": f'["{tmp_path}"]',
        "KILN_TEST_MARK_DIR": str(marks),
        "KILN_WORKER__EXIT_VIA_OS_EXIT": "true" if trick else "false",
    }
    server = kiln_server("--workers", "1", env=env, app="exit_app:app")
    client = server.client()
    pid = client.wait(client.submit("whoami"), timeout_s=15)["job"]["result"]  # type: ignore[index]
    server.signal(signal.SIGTERM)
    assert server.wait_exit() == 0
    return (marks / f"atexit-{pid}").exists(), (marks / f"final_gc-{pid}").exists()


@pytest.mark.parametrize("start_method", ["spawn"], indirect=True)
def test_spawned_worker_without_the_trick_runs_everything(
    kiln_server: Start, tmp_path: Path, start_method: str
) -> None:
    """Control: a normal interpreter exit runs atexit handlers AND finalization."""
    assert run_and_stop(kiln_server, tmp_path, trick=False) == (True, True)


@pytest.mark.parametrize("start_method", ["spawn"], indirect=True)
def test_spawned_worker_with_the_trick_skips_only_finalization(
    kiln_server: Start, tmp_path: Path, start_method: str
) -> None:
    """With the trick: the app's atexit handler still runs (ours runs last), finalization doesn't."""
    assert run_and_stop(kiln_server, tmp_path, trick=True) == (True, False)


@pytest.mark.parametrize("start_method", ["fork", "forkserver"], indirect=True)
@pytest.mark.parametrize("trick", [False, True])
def test_forked_workers_skip_both_anyway(
    kiln_server: Start, tmp_path: Path, start_method: str, trick: bool
) -> None:
    """ADR-028: forked workers end with os._exit, so neither runs, with or without the trick."""
    assert run_and_stop(kiln_server, tmp_path, trick=trick) == (False, False)
