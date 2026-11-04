"""Tests for confirm.py's pure helpers: outcome parsing, normalising, the temp-copy source."""

import ast

import pytest

from kiln.forkcheck.confirm import (
    Confirmation,
    MethodRun,
    make_copy_source,
    normalise,
    outcome_of,
)

TRACEBACK = """Traceback (most recent call last):
  File "x.py", line 1, in <module>
    raise thing
_pickle.PicklingError: Can't pickle <function f.<locals>.g at 0x7f00>
"""


@pytest.mark.parametrize(
    ("code", "stderr", "expected"),
    [
        (0, "", "ok"),
        (-9, "", "signal:SIGKILL"),
        (1, TRACEBACK, "exception:PicklingError"),
        (
            1,
            "Traceback (most recent call last):\n  File ...\nKeyboardInterrupt\n",
            "exception:KeyboardInterrupt",
        ),
        (3, "plain error text\n", "exit:3"),
    ],
    ids=["ok", "signal", "exception", "bare-exception", "exit"],
)
def test_outcome_of(code: int, stderr: str, expected: str) -> None:
    """Return codes and tracebacks become the outcome strings of §5.9."""
    assert outcome_of(code, stderr) == expected


def test_normalise_hides_addresses_pids_and_timings() -> None:
    """Volatile parts are replaced, so two identical runs compare equal."""
    text = "obj at 0x7f3a2b, pid 4242 (PID=17) took 12.5 ms"
    assert normalise(text) == "obj at <ADDR>, pid <PID> (PID=<PID>) took <MS>"


def test_copy_source_keeps_docstring_and_future_import_first() -> None:
    """The preamble goes after the docstring and `from __future__` (else a SyntaxError)."""
    source = '"""Doc."""\nfrom __future__ import annotations\nprint(1)\n'
    copy = make_copy_source(source, "spawn")
    lines = copy.splitlines()
    assert lines[:2] == ['"""Doc."""', "from __future__ import annotations"]
    assert "set_start_method('spawn', force=True)" in lines[3]
    assert lines[-1] == "print(1)"
    ast.parse(copy)


def test_copy_source_without_docstring_starts_with_preamble() -> None:
    """With nothing to keep first, the preamble is the first thing in the file."""
    copy = make_copy_source("print(1)\n", "forkserver")
    assert copy.startswith("import multiprocessing as _kiln_confirm_mp\n")


def test_differs_is_reported_in_the_json_shape() -> None:
    """as_dict() has one outcome per method plus `differs`."""
    runs = {m: MethodRun(m, "ok", "") for m in ("fork", "forkserver", "spawn")}
    assert Confirmation(runs, False).as_dict() == {
        "fork": "ok",
        "forkserver": "ok",
        "spawn": "ok",
        "differs": False,
    }
