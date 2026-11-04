"""Structured logging for every Kiln process: one event per line, `key=value` or JSON.

WHY THIS MODULE EXISTS
    Kiln is several processes writing to one terminal at once. To make sense of that, every
    line must say which process wrote it (`role`, `pid`, `worker_id`) and *what* happened, as
    a stable event name (`event=worker_died`) that tests and lab scripts can search for. The
    format is defined in docs/spec/06-observability.md §1; the event names in §2.

HOW IT FITS
    arbiter.py / worker/main.py ──configure(role, ...)──▶ one handler on stderr (or log.file)
    any module ──event(log, "worker_ready", worker_id=2, ...)──▶ one formatted line

READ FIRST
    docs/python/logging-basics.md, docs/spec/06-observability.md §1,
    docs/concepts/fork-and-threads.md
    (why each worker re-runs configure after fork)

MILESTONE
    Introduced in K1.
"""

import datetime
import json
import logging
import os
import sys
from typing import TextIO

# WHY: every Kiln logger lives under the "kiln" name, so configure() only touches our own
# loggers and never the root logger that other libraries share.
ROOT_LOGGER = "kiln"
# WHY: spec/06 §1.5: lines below PIPE_BUF (4096 bytes) are written atomically to a pipe, so
# lines from different processes never mix *within* a line. Longer values are cut short.
MAX_VALUE_CHARS = 1500


def _encode_value(value: object) -> str:
    """Write one field value the way spec/06 §1.2 says: bare if simple, quoted otherwise.

    Why:
        `grep event=worker_died` must always work, so a value can never contain a raw
        newline or an unescaped quote that would break the one-event-per-line rule.

    Args:
        value: any field value.

    Returns:
        The encoded text.

    Example:
        >>> _encode_value("plain"), _encode_value("two words"), _encode_value(None)
        ('plain', '"two words"', 'null')
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if len(text) > MAX_VALUE_CHARS:
        text = text[:MAX_VALUE_CHARS] + "…(truncated)"
    needs_quotes = text == "" or any(ch in text for ch in ' ="\\\n\t')
    if not needs_quotes:
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return '"' + escaped.replace("\t", "\\t") + '"'


class KilnFormatter(logging.Formatter):
    """Turn a log record into one `kv` or `json` line with Kiln's common fields.

    Why:
        The logging module separates "what happened" (the record) from "how it looks" (the
        formatter). Putting Kiln's format in one formatter keeps every call site simple.
    """

    def __init__(self, role: str, fmt: str, worker_id: int | None) -> None:
        """Remember the fields that are the same on every line of this process.

        Args:
            role: "master", "worker", "gateway" or "forkserver".
            fmt: "kv" or "json".
            worker_id: set in workers, so every worker line carries it.
        """
        super().__init__()
        self.role = role
        self.fmt = fmt
        self.worker_id = worker_id

    def format(self, record: logging.LogRecord) -> str:
        """Build the line for one record.

        Why:
            Called by the logging machinery for every record that passes the level filter.

        Args:
            record: the log record; Kiln events carry their fields in `record.kv`.

        Returns:
            The formatted line, without a trailing newline.
        """
        stamp = datetime.datetime.fromtimestamp(record.created, datetime.UTC)
        fields: dict[str, object] = {
            "ts": stamp.isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "role": self.role,
            "pid": os.getpid(),
        }
        if self.worker_id is not None:
            fields["worker_id"] = self.worker_id
        # WHY: our event() helper passes the event name as the message and the fields in
        # extra={"kv": ...}; plain log.info("text") calls still work and become msg=...
        extra = getattr(record, "kv", None)
        if isinstance(extra, dict):
            fields["event"] = record.getMessage()
            fields.update(extra)
        else:
            fields["msg"] = record.getMessage()
        if record.exc_info:
            fields["traceback"] = self.formatException(record.exc_info)
        if self.fmt == "json":
            return json.dumps(fields, default=str, ensure_ascii=False)
        return " ".join(f"{key}={_encode_value(value)}" for key, value in fields.items())


def configure(
    role: str,
    level: str = "INFO",
    fmt: str = "kv",
    log_file: str = "",
    worker_id: int | None = None,
    stream: TextIO | None = None,
) -> None:
    """Set up logging for this process, replacing any handlers configured before.

    Why:
        Each process calls this once at startup. A forked worker calls it *again*, because it
        inherited the master's handler and formatter (role=master) and needs its own
        (role=worker, worker_id=N). See docs/spec/06-observability.md §1.4.

    Args:
        role: which kind of Kiln process this is.
        level: the lowest level to print ("DEBUG", "INFO", ...).
        fmt: "kv" or "json".
        log_file: a file to append to; "" means stderr.
        worker_id: the worker's id, for worker processes.
        stream: an explicit stream (tests pass an io.StringIO); overrides log_file.
    """
    logger = logging.getLogger(ROOT_LOGGER)
    for old in list(logger.handlers):
        logger.removeHandler(old)
        old.close()
    handler: logging.Handler
    if stream is not None:
        handler = logging.StreamHandler(stream)
    elif log_file:
        # WHY: append mode ("a" → O_APPEND): every write lands at the end of the file, even
        # with several processes writing (spec/06 §1.5).
        handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(KilnFormatter(role, fmt, worker_id))
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    # WHY: don't also pass records up to the root logger, or they'd be printed twice.
    logger.propagate = False


def event(log: logging.Logger, level: int, name: str, **fields: object) -> None:
    """Log one catalogue event (docs/spec/06-observability.md §2) with its fields.

    Why:
        One helper means every event has the same shape, and the level check happens before
        any work, so DEBUG events cost almost nothing when DEBUG is off (code-style §5).

    Args:
        log: the module's logger (`logging.getLogger(__name__)`).
        level: logging.INFO, logging.WARNING, ...
        name: the stable event name, such as "worker_ready".
        **fields: the event's fields.

    Example:
        >>> import io
        >>> buf = io.StringIO()
        >>> configure("master", stream=buf)
        >>> event(logging.getLogger("kiln.demo"), logging.INFO, "demo_event", answer=42)
        >>> "event=demo_event answer=42" in buf.getvalue()
        True
    """
    # PY: **fields collects every keyword argument into a dict; see docs/python/functions.md.
    if log.isEnabledFor(level):
        log.log(level, name, extra={"kv": fields})


def flush_all() -> None:
    """Flush every Kiln log handler.

    Why:
        Before os.fork(), anything still waiting in a handler's buffer would be copied into
        the child and printed twice (the fork launcher's GOTCHA, docs/concepts/fork.md).
    """
    for handler in logging.getLogger(ROOT_LOGGER).handlers:
        handler.flush()
