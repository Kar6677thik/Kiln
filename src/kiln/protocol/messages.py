"""KP/1 messages: build outgoing ones, and turn untrusted incoming dicts into typed requests.

WHY THIS MODULE EXISTS
    A frame decodes into a plain dict, and a dict from the network can contain *anything*:
    a missing field, a string where a number belongs, `true` where an id belongs. Code that
    reads such a dict directly is full of hidden crashes. This module checks every field once
    and returns small, typed, read-only objects (`Submit`, `Run`, `Ready`...), so the rest of
    Kiln only ever sees valid data. The rules are those of docs/spec/02-protocol.md §3–4.

HOW IT FITS
    master/arbiter.py ──parse_client_request(dict)──▶ Submit / Wait / ... or BadRequest
    master/arbiter.py ──parse_worker_message(dict)──▶ Ready / Heartbeat / Result / ...
    worker/main.py    ──parse_master_message(dict)──▶ Run / Shutdown
    everyone          ──ok(...), error(...), message(...)──▶ dicts for encode_frame()

READ FIRST
    docs/spec/02-protocol.md §3–4, docs/python/slots-and-frozen-dataclasses.md,
    docs/python/enum.md

MILESTONE
    Introduced in K1 (debug and forkserver messages are only named here; K2/K6 use them).
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum

from kiln.errors import ErrorCode, KilnError

# WHY: re-exported so protocol users can write `from kiln.protocol.messages import ErrorCode`.
__all__ = ["ErrorCode"]

MAX_WAIT_TIMEOUT_S = 300.0  # spec/02 §3.1: wait.timeout_s ≤ 300
DEFAULT_WAIT_TIMEOUT_S = 30.0


class MsgType(StrEnum):
    """Every `type` value of KP/1 (docs/spec/02-protocol.md §3–4).

    Why:
        One list of names shared by every sender and receiver; a typo is an error, not a
        silently unknown message.
    """

    # replies
    OK = "ok"
    ERROR = "error"
    # client → master
    PING = "ping"
    SUBMIT = "submit"
    STATUS = "status"
    WAIT = "wait"
    CANCEL = "cancel"
    STATS = "stats"
    DEBUG = "debug"
    # worker → master
    READY = "ready"
    HEARTBEAT = "heartbeat"
    RESULT = "result"
    RETIRING = "retiring"
    BOOT_FAILED = "boot_failed"
    DEBUG_REPLY = "debug_reply"
    # master → worker
    RUN = "run"
    SHUTDOWN = "shutdown"
    # master ↔ forkserver (K2)
    SPAWN_WORKER = "spawn_worker"
    SPAWNED = "spawned"
    CHILD_EXIT = "child_exit"


class BadRequest(KilnError):
    """A message was a valid frame but its fields are wrong.

    Why:
        The master answers this with `error{code: BAD_REQUEST}` and keeps the connection open
        (spec/02 §2.1); the message names the field so the client author can fix it.
    """

    def __init__(self, field_name: str, reason: str) -> None:
        """Record which field is wrong and why.

        Args:
            field_name: the offending field, such as "max_attempts".
            reason: what's wrong with it, such as "must be an integer ≥ 1".
        """
        super().__init__(f"{field_name}: {reason}", ErrorCode.BAD_REQUEST)
        self.field_name = field_name


# --- Typed messages ----------------------------------------------------------------------------
# PY: frozen=True makes instances read-only, slots=True makes them small and stops typos like
# req.tsak = ... from silently creating a new attribute.
# See docs/python/slots-and-frozen-dataclasses.md.


@dataclass(frozen=True, slots=True)
class Ping:
    """Client asks "are you alive?".

    Why:
        The simplest request: health checks and the client's connection test.
    """

    req_id: int


@dataclass(frozen=True, slots=True)
class Submit:
    """Client asks the master to queue one job (spec/02 §3.1).

    Why:
        The heart of the protocol; every field has been validated by parse_client_request.
    """

    req_id: int
    task: str
    args: list[object]
    kwargs: dict[str, object]
    timeout_s: float | None
    max_attempts: int | None


@dataclass(frozen=True, slots=True)
class Status:
    """Client asks for a job's current JobView.

    Why:
        Polling for state without blocking.
    """

    req_id: int
    job_id: str


@dataclass(frozen=True, slots=True)
class Wait:
    """Client asks to be answered when the job is terminal (or after timeout_s).

    Why:
        A long-poll: the master holds the reply instead of the client asking repeatedly.
    """

    req_id: int
    job_id: str
    timeout_s: float


@dataclass(frozen=True, slots=True)
class Cancel:
    """Client asks to cancel a job that is still QUEUED.

    Why:
        Only queued jobs can be cancelled safely (spec/00 §4 non-goals).
    """

    req_id: int
    job_id: str


@dataclass(frozen=True, slots=True)
class Stats:
    """Client asks for the StatsView (spec/06 §3).

    Why:
        `kiln stats` and the load generator read master-wide counters this way.
    """

    req_id: int


@dataclass(frozen=True, slots=True)
class Debug:
    """Client asks for a diagnostic operation (K6/K7; refused unless debug.enabled).

    Why:
        Parsed now so a K1 master can answer DEBUG_DISABLED cleanly instead of BAD_REQUEST.
    """

    req_id: int
    target: str | int
    op: str
    params: dict[str, object]


@dataclass(frozen=True, slots=True)
class Ready:
    """Worker announces it has booted, with its capacity and task manifest (spec/02 §4.1).

    Why:
        Until this arrives the master sends the worker nothing; the manifest is how the master
        learns which task names exist (ADR-013).
    """

    worker_id: int
    pid: int
    generation: int
    capacity: int
    executor: str
    manifest: dict[str, object]
    python: dict[str, object]


@dataclass(frozen=True, slots=True)
class Heartbeat:
    """Worker's periodic "still alive", with its memory and progress.

    Why:
        Silence for heartbeat_timeout_s means the worker is hung (spec/04 §5.4).
    """

    rss_bytes: int
    jobs_done: int
    inflight: int
    gil_enabled: bool


@dataclass(frozen=True, slots=True)
class Result:
    """Worker reports how one attempt of one job ended.

    Why:
        (job_id, attempt) lets the master ignore late results from attempts it already gave up
        on (spec/03 §4.1, "late result").
    """

    job_id: str
    attempt: int
    ok: bool
    value: object
    error: dict[str, object] | None
    duration_ms: float
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class Retiring:
    """Worker announces it will exit after its in-flight jobs (max_jobs or max_rss).

    Why:
        The master stops sending it work and starts a replacement right away (spec/04 §5.5).
    """

    reason: str


@dataclass(frozen=True, slots=True)
class BootFailed:
    """Worker couldn't import the app; it exits with code 3 right after sending this.

    Why:
        Gives the master the real Python error to log, instead of only "exit code 3".
    """

    error: dict[str, object]


@dataclass(frozen=True, slots=True)
class Run:
    """Master tells a worker to execute one attempt of one job.

    Why:
        Everything the worker needs, and nothing else: the worker never sees the job table.
    """

    job_id: str
    attempt: int
    task: str
    args: list[object]
    kwargs: dict[str, object]
    timeout_s: float


@dataclass(frozen=True, slots=True)
class Shutdown:
    """Master tells a worker to stop: gracefully (finish in-flight first) or now.

    Why:
        Graceful retirement is a message; forced termination is a signal (spec/04 §5.6).
    """

    graceful: bool = True


# PY: these are type aliases for "one of these classes" (a union type).
# See docs/python/type-hints.md.
ClientRequest = Ping | Submit | Status | Wait | Cancel | Stats | Debug
WorkerMessage = Ready | Heartbeat | Result | Retiring | BootFailed
MasterMessage = Run | Shutdown


# --- Field checkers ----------------------------------------------------------------------------


def _int(msg: dict[str, object], key: str, *, minimum: int | None = None) -> int:
    """Read a required integer field.

    Why:
        GOTCHA: in Python, True and False are ints (isinstance(True, int) is True), so a
        careless check would accept `"req_id": true`. We reject bools explicitly.

    Args:
        msg: the incoming message.
        key: the field name.
        minimum: the smallest allowed value, if any.

    Returns:
        The integer.

    Raises:
        BadRequest: missing, not an int, a bool, or below minimum.
    """
    value = msg.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise BadRequest(key, "must be an integer")
    if minimum is not None and value < minimum:
        raise BadRequest(key, f"must be ≥ {minimum}")
    return value


def _str(msg: dict[str, object], key: str) -> str:
    """Read a required non-empty string field.

    Args:
        msg: the incoming message.
        key: the field name.

    Returns:
        The string.

    Raises:
        BadRequest: missing, not a string, or empty.
    """
    value = msg.get(key)
    if not isinstance(value, str) or value == "":
        raise BadRequest(key, "must be a non-empty string")
    return value


def _number(msg: dict[str, object], key: str, default: float | None) -> float | None:
    """Read an optional positive number (int or float, never bool).

    Why:
        Timeouts must be positive numbers; bools are rejected because Python treats True as the
        number 1.

    Args:
        msg: the incoming message.
        key: the field name.
        default: returned when the field is absent or null.

    Returns:
        The number as a float, or the default.

    Raises:
        BadRequest: present but not a positive number.
    """
    value = msg.get(key)
    if value is None:
        return default
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        raise BadRequest(key, "must be a number > 0")
    return float(value)


def _list(msg: dict[str, object], key: str) -> list[object]:
    """Read an optional list field (default empty).

    Args:
        msg: the incoming message.
        key: the field name.

    Returns:
        The list.

    Raises:
        BadRequest: present but not a list.
    """
    value = msg.get(key, [])
    if not isinstance(value, list):
        raise BadRequest(key, "must be a list")
    return value


def _dict(msg: dict[str, object], key: str, required: bool = False) -> dict[str, object]:
    """Read an object field (default empty unless required).

    Why:
        args/kwargs/manifest must be JSON objects; checking once here means later code can use them
        safely.

    Args:
        msg: the incoming message.
        key: the field name.
        required: whether the field must be present.

    Returns:
        The dict.

    Raises:
        BadRequest: missing (when required) or not an object.
    """
    if required and key not in msg:
        raise BadRequest(key, "is required")
    value = msg.get(key, {})
    if not isinstance(value, dict):
        raise BadRequest(key, "must be an object")
    return value


def _bool(msg: dict[str, object], key: str, default: bool) -> bool:
    """Read an optional boolean field.

    Args:
        msg: the incoming message.
        key: the field name.
        default: returned when absent.

    Returns:
        The boolean.

    Raises:
        BadRequest: present but not true/false.
    """
    value = msg.get(key, default)
    if not isinstance(value, bool):
        raise BadRequest(key, "must be true or false")
    return value


# --- Parsers: client → master ------------------------------------------------------------------


def _parse_submit(msg: dict[str, object], req_id: int) -> Submit:
    """Validate a `submit` request (spec/02 §3.1, spec/03 §2.1).

    Why:
        The most detailed request; kept separate so the dispatch table stays a table.

    Args:
        msg: the request.
        req_id: its already-validated req_id.

    Returns:
        The typed request.
    """
    max_attempts = msg.get("max_attempts")
    if max_attempts is not None:
        max_attempts = _int(msg, "max_attempts", minimum=1)
    return Submit(
        req_id=req_id,
        task=_str(msg, "task"),
        args=_list(msg, "args"),
        kwargs=_dict(msg, "kwargs"),
        timeout_s=_number(msg, "timeout_s", None),
        max_attempts=max_attempts,
    )


def _parse_wait(msg: dict[str, object], req_id: int) -> Wait:
    """Validate a `wait` request: timeout_s defaults to 30 and may not exceed 300.

    Args:
        msg: the request.
        req_id: its req_id.

    Returns:
        The typed request.
    """
    timeout_s = _number(msg, "timeout_s", DEFAULT_WAIT_TIMEOUT_S) or DEFAULT_WAIT_TIMEOUT_S
    if timeout_s > MAX_WAIT_TIMEOUT_S:
        raise BadRequest("timeout_s", f"must be ≤ {MAX_WAIT_TIMEOUT_S:g}")
    return Wait(req_id=req_id, job_id=_str(msg, "job_id"), timeout_s=timeout_s)


def _parse_debug(msg: dict[str, object], req_id: int) -> Debug:
    """Validate a `debug` request: target is "master" or a worker id.

    Args:
        msg: the request.
        req_id: its req_id.

    Returns:
        The typed request.
    """
    target = msg.get("target")
    if target != "master" and (not isinstance(target, int) or isinstance(target, bool)):
        raise BadRequest("target", 'must be "master" or a worker id')
    return Debug(req_id=req_id, target=target, op=_str(msg, "op"), params=_dict(msg, "params"))


def _parse_ping(msg: dict[str, object], req_id: int) -> Ping:
    """Validate a `ping` (it has no other fields).

    Args:
        msg: the request.
        req_id: its req_id.

    Returns:
        The typed request.
    """
    return Ping(req_id)


def _parse_status(msg: dict[str, object], req_id: int) -> Status:
    """Validate a `status` request.

    Args:
        msg: the request.
        req_id: its req_id.

    Returns:
        The typed request.
    """
    return Status(req_id, _str(msg, "job_id"))


def _parse_cancel(msg: dict[str, object], req_id: int) -> Cancel:
    """Validate a `cancel` request.

    Args:
        msg: the request.
        req_id: its req_id.

    Returns:
        The typed request.
    """
    return Cancel(req_id, _str(msg, "job_id"))


def _parse_stats(msg: dict[str, object], req_id: int) -> Stats:
    """Validate a `stats` request (no other fields).

    Args:
        msg: the request.
        req_id: its req_id.

    Returns:
        The typed request.
    """
    return Stats(req_id)


# WHY: an explicit table from type name to parser (code-style §1 prefers this over looking up
# functions by building their names as strings).
_CLIENT_PARSERS: dict[str, Callable[[dict[str, object], int], ClientRequest]] = {
    MsgType.PING: _parse_ping,
    MsgType.SUBMIT: _parse_submit,
    MsgType.STATUS: _parse_status,
    MsgType.WAIT: _parse_wait,
    MsgType.CANCEL: _parse_cancel,
    MsgType.STATS: _parse_stats,
    MsgType.DEBUG: _parse_debug,
}


def request_id_of(msg: dict[str, object]) -> int | None:
    """Return the message's req_id if it is a valid integer, else None.

    Why:
        Even a bad request deserves an error reply carrying its req_id, when it has one, so
        the client can match the error to the request.

    Args:
        msg: the incoming message.

    Returns:
        The req_id or None.
    """
    value = msg.get("req_id")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def parse_client_request(msg: dict[str, object]) -> ClientRequest:
    """Turn an untrusted client message into a typed request.

    Why:
        The only door through which client input enters the master: after this, every field
        has the right type and range.

    Args:
        msg: a decoded frame from a client connection.

    Returns:
        One of Ping, Submit, Status, Wait, Cancel, Stats, Debug.

    Raises:
        BadRequest: unknown type or any invalid field.

    Example:
        >>> parse_client_request({"type": "ping", "req_id": 1})
        Ping(req_id=1)
    """
    kind = msg.get("type")
    parser = _CLIENT_PARSERS.get(kind) if isinstance(kind, str) else None
    if parser is None:
        raise BadRequest("type", f"unknown request type {kind!r}")
    return parser(msg, _int(msg, "req_id"))


# --- Parsers: worker → master and master → worker ----------------------------------------------


def _parse_ready(msg: dict[str, object]) -> Ready:
    """Validate a worker's `ready` message.

    Why:
        The ready message carries the manifest the master trusts for validation, so every field is
        checked.

    Args:
        msg: the message.

    Returns:
        The typed message.
    """
    return Ready(
        worker_id=_int(msg, "worker_id", minimum=0),
        pid=_int(msg, "pid", minimum=1),
        generation=_int(msg, "generation", minimum=0),
        capacity=_int(msg, "capacity", minimum=1),
        executor=_str(msg, "executor"),
        manifest=_dict(msg, "manifest", required=True),
        python=_dict(msg, "python"),
    )


def _parse_heartbeat(msg: dict[str, object]) -> Heartbeat:
    """Validate a worker's `heartbeat`.

    Why:
        Heartbeat values feed recycling and hang detection, so wrong types must be caught at the
        boundary.

    Args:
        msg: the message.

    Returns:
        The typed message.
    """
    return Heartbeat(
        rss_bytes=_int(msg, "rss_bytes", minimum=0),
        jobs_done=_int(msg, "jobs_done", minimum=0),
        inflight=_int(msg, "inflight", minimum=0),
        gil_enabled=_bool(msg, "gil_enabled", True),
    )


def _parse_result(msg: dict[str, object]) -> Result:
    """Validate a worker's `result`.

    Why:
        A failed result must say what failed; ok=false with no error details would leave the job's
        error empty.

    Args:
        msg: the message.

    Returns:
        The typed message.
    """
    ok = _bool(msg, "ok", False)
    error = None if ok else _dict(msg, "error", required=True)
    duration = msg.get("duration_ms", 0.0)
    if not isinstance(duration, int | float) or isinstance(duration, bool):
        raise BadRequest("duration_ms", "must be a number")
    return Result(
        job_id=_str(msg, "job_id"),
        attempt=_int(msg, "attempt", minimum=1),
        ok=ok,
        value=msg.get("value"),
        error=error,
        duration_ms=float(duration),
        timed_out=_bool(msg, "timed_out", False),
    )


def parse_worker_message(msg: dict[str, object]) -> WorkerMessage:
    """Turn a message from a worker's channel into a typed message.

    Why:
        Workers are our own code, but a bug there must be caught at the boundary, not deep
        inside the master (spec/02 §2.1: a bad worker message is logged and ignored).

    Args:
        msg: a decoded frame from a worker channel.

    Returns:
        One of Ready, Heartbeat, Result, Retiring, BootFailed.

    Raises:
        BadRequest: unknown type or invalid fields.
    """
    kind = msg.get("type")
    if kind == MsgType.READY:
        return _parse_ready(msg)
    if kind == MsgType.HEARTBEAT:
        return _parse_heartbeat(msg)
    if kind == MsgType.RESULT:
        return _parse_result(msg)
    if kind == MsgType.RETIRING:
        return Retiring(reason=_str(msg, "reason"))
    if kind == MsgType.BOOT_FAILED:
        return BootFailed(error=_dict(msg, "error", required=True))
    raise BadRequest("type", f"unknown worker message type {kind!r}")


def parse_master_message(msg: dict[str, object]) -> MasterMessage:
    """Turn a message from the master into a typed message (worker side).

    Why:
        The worker validates what it receives too: defence in depth costs little here.

    Args:
        msg: a decoded frame from the master.

    Returns:
        Run or Shutdown.

    Raises:
        BadRequest: unknown type or invalid fields.
    """
    kind = msg.get("type")
    if kind == MsgType.RUN:
        timeout_s = _number(msg, "timeout_s", None)
        if timeout_s is None:
            raise BadRequest("timeout_s", "is required")
        return Run(
            job_id=_str(msg, "job_id"),
            attempt=_int(msg, "attempt", minimum=1),
            task=_str(msg, "task"),
            args=_list(msg, "args"),
            kwargs=_dict(msg, "kwargs"),
            timeout_s=timeout_s,
        )
    if kind == MsgType.SHUTDOWN:
        return Shutdown(graceful=_bool(msg, "graceful", True))
    raise BadRequest("type", f"unknown master message type {kind!r}")


# --- Builders ----------------------------------------------------------------------------------


def ok(req_id: int, **fields: object) -> dict[str, object]:
    """Build an `ok` reply.

    Args:
        req_id: the request being answered.
        **fields: the reply's data.

    Returns:
        The reply dict.

    Example:
        >>> ok(7, job_id="abc")
        {'type': 'ok', 'req_id': 7, 'job_id': 'abc'}
    """
    return {"type": str(MsgType.OK), "req_id": req_id, **fields}


def error(req_id: int | None, code: ErrorCode, message: str, **extra: object) -> dict[str, object]:
    """Build an `error` reply.

    Why:
        Every error reply has the same shape (spec/02 §3.2), so clients can handle all of them the
        same way.

    Args:
        req_id: the request being answered, or None if it had no valid req_id.
        code: the error code (spec/02 §3.3).
        message: a human explanation.
        **extra: extra fields such as retry_after_s.

    Returns:
        The reply dict.
    """
    return {
        "type": str(MsgType.ERROR),
        "req_id": req_id,
        "code": str(code),
        "message": message,
        **extra,
    }


def message(kind: MsgType, payload: object | None = None, **fields: object) -> dict[str, object]:
    """Build any other message from a typed dataclass and/or extra fields.

    Why:
        The typed classes above double as the message layout: asdict() turns Run(...) into
        exactly the fields spec/02 §4 lists, so senders and receivers can't drift apart.

    Args:
        kind: the message type.
        payload: a message dataclass instance (Run, Ready, ...) or None.
        **fields: additional fields.

    Returns:
        A dict ready for encode_frame().

    Example:
        >>> message(MsgType.SHUTDOWN, Shutdown(graceful=True))
        {'type': 'shutdown', 'graceful': True}
    """
    data: dict[str, object] = {"type": str(kind)}
    if payload is not None:
        # PY: asdict() turns a dataclass instance into a dict of its fields.
        data.update(asdict(payload))  # type: ignore[call-overload]  # WHY: payload is a dataclass
    data.update(fields)
    return data
