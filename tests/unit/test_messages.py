"""Tests for KP/1 message validation and building (docs/spec/02-protocol.md §3–4)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kiln.protocol.messages import (
    BadRequest,
    MsgType,
    Ping,
    Ready,
    Result,
    Run,
    Shutdown,
    Submit,
    Wait,
    error,
    message,
    ok,
    parse_client_request,
    parse_master_message,
    parse_worker_message,
)
from kiln.protocol.messages import ErrorCode as Code


def test_submit_with_defaults() -> None:
    """A minimal submit gets empty args/kwargs and no overrides (spec/02 §3.1)."""
    request = parse_client_request({"type": "submit", "req_id": 1, "task": "add"})
    assert request == Submit(1, "add", [], {}, None, None)


@pytest.mark.parametrize(
    ("msg", "field"),
    [
        ({"type": "submit", "req_id": 1}, "task"),
        ({"type": "submit", "req_id": 1, "task": "add", "args": {}}, "args"),
        ({"type": "submit", "req_id": 1, "task": "add", "kwargs": []}, "kwargs"),
        ({"type": "submit", "req_id": 1, "task": "add", "max_attempts": 0}, "max_attempts"),
        ({"type": "submit", "req_id": 1, "task": "add", "timeout_s": -1}, "timeout_s"),
        ({"type": "submit", "req_id": True, "task": "add"}, "req_id"),
        ({"type": "submit", "req_id": 1, "task": "add", "max_attempts": True}, "max_attempts"),
        ({"type": "wait", "req_id": 1, "job_id": "x", "timeout_s": 301}, "timeout_s"),
        ({"type": "status", "req_id": 1}, "job_id"),
        ({"type": "teleport", "req_id": 1}, "type"),
        ({"req_id": 1}, "type"),
    ],
)
def test_invalid_requests_name_the_bad_field(msg: dict[str, object], field: str) -> None:
    """Each invalid request raises BadRequest naming the offending field.

    Includes the bool-vs-int gotcha: in Python `True` is an int, but `"req_id": true` must be
    rejected (K1 spec §5.2).
    """
    with pytest.raises(BadRequest) as info:
        parse_client_request(msg)
    assert info.value.field_name == field
    assert info.value.code is Code.BAD_REQUEST


def test_wait_defaults_to_30_seconds() -> None:
    """A wait without timeout_s waits 30 s by default (spec/02 §3.1)."""
    assert parse_client_request({"type": "wait", "req_id": 2, "job_id": "j"}) == Wait(2, "j", 30.0)


@given(
    st.dictionaries(
        st.sampled_from(["type", "req_id", "task", "args", "kwargs", "job_id", "timeout_s"]),
        st.none()
        | st.booleans()
        | st.integers()
        | st.floats(allow_nan=False)
        | st.text()
        | st.lists(st.integers()),
    )
)
def test_parser_never_raises_anything_but_bad_request(msg: dict[str, object]) -> None:
    """Whatever a client sends, the parser either succeeds or raises BadRequest, never a crash.

    That's the property that keeps one malformed request from taking down the master.
    """
    try:
        parse_client_request(msg)
    except BadRequest:
        pass


def test_worker_messages_round_trip_through_message_builder() -> None:
    """message(Type, dataclass) produces exactly what the parser accepts (spec/02 §4)."""
    ready = Ready(0, 123, 0, 1, "sync", {"tasks": {}}, {"version": "3.15"})
    assert parse_worker_message(message(MsgType.READY, ready)) == ready
    result = Result("j", 1, True, 5, None, 0.2)
    assert parse_worker_message(message(MsgType.RESULT, result)) == result
    run = Run("j", 1, "add", [2, 3], {}, 30.0)
    assert parse_master_message(message(MsgType.RUN, run)) == run
    assert parse_master_message(message(MsgType.SHUTDOWN, Shutdown(True))) == Shutdown(True)


def test_failed_result_requires_error_details() -> None:
    """A result with ok=false must say what went wrong."""
    with pytest.raises(BadRequest):
        parse_worker_message({"type": "result", "job_id": "j", "attempt": 1, "ok": False})


def test_reply_builders() -> None:
    """ok() and error() build the reply shapes of spec/02 §3.2."""
    assert ok(7, job_id="x") == {"type": "ok", "req_id": 7, "job_id": "x"}
    assert error(7, Code.QUEUE_FULL, "full", retry_after_s=1.0) == {
        "type": "error",
        "req_id": 7,
        "code": "QUEUE_FULL",
        "message": "full",
        "retry_after_s": 1.0,
    }
    assert parse_client_request({"type": "ping", "req_id": 3}) == Ping(3)
