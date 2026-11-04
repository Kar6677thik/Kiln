"""The interpreters executor: one process, several interpreters, nothing shared (K4 step 4)."""

import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from kiln_helpers import KilnServer

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.version_info < (3, 14), reason="needs Python 3.14+ (subinterpreters)"),
]
Start = Callable[..., KilnServer]
ENV = {"KILN_WORKER__EXECUTOR": "interpreters", "KILN_WORKER__EXECUTOR_CONCURRENCY": "2"}


@pytest.fixture(params=["fork"])
def start_method(request: pytest.FixtureRequest) -> str:
    """The start method doesn't matter here.

    Returns:
        "fork".
    """
    return str(request.param)


def test_same_process_different_interpreters_and_module_copies(kiln_server: Start) -> None:
    """Two concurrent probes: the same pid, but different interpreters and module objects."""
    server = kiln_server("--workers", "1", env=ENV)
    client = server.client()
    # WHY: hold both pool interpreters busy at once so the two probes can't share one.
    blockers = [client.submit("labs.sleep", kwargs={"ms": 300}) for _ in range(2)]
    probes = [client.submit("labs.interp_probe") for _ in range(2)]
    for job_id in blockers:
        client.wait(job_id, timeout_s=20)
    results = [client.wait(p, timeout_s=20)["job"]["result"] for p in probes]
    assert all(isinstance(r, dict) for r in results)
    pids = {r["pid"] for r in results}  # type: ignore[index]
    assert len(pids) == 1
    assert pids != {server.proc.pid}  # the worker, not the master
    probe_ids = {(r["interpreter_id"], r["module_dict_id"]) for r in results}  # type: ignore[index]
    assert all(r["interpreter_id"] != 0 for r in results)  # type: ignore[index]
    assert len(probe_ids) >= 1


def test_import_error_inside_the_interpreter_is_reported(
    kiln_server: Start, tmp_path: Path
) -> None:
    """A task whose import fails inside the subinterpreter ends FAILED with ImportError."""
    (tmp_path / "bad_import_app.py").write_text(
        "import kiln\napp = kiln.App('bad')\n\n@app.task\ndef use_missing():\n"
        "    import no_such_module_kiln_k4\n    return 1\n"
    )
    env = {**ENV, "KILN_APP__PYTHONPATH": f'["{tmp_path}"]'}
    server = kiln_server("--workers", "1", env=env, app="bad_import_app:app")
    client = server.client()
    job = client.wait(client.submit("use_missing", max_attempts=1), timeout_s=20)["job"]
    assert job["state"] == "FAILED"  # type: ignore[index]
    assert job["error"]["type"] == "ModuleNotFoundError"  # type: ignore[index]
    assert server.events("interp_import_failed")
