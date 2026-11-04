"""Kiln's shared error vocabulary: the KP/1 error codes and the KilnError base exception.

WHY THIS MODULE EXISTS
    The master, the CLI client and (from K5) the gateway must all agree on what "QUEUE_FULL"
    or "NOT_FOUND" means. Defining the codes once, as an enum, means a typo becomes an
    AttributeError instead of a silently different string on the wire. Every Kiln-specific
    exception also derives from one base class, so callers can catch "anything Kiln raised"
    with a single `except KilnError`.

HOW IT FITS
    errors.py  ← the "core layer": imports nothing from Kiln, and everything may import it
       ▲ used by protocol/messages.py (error replies), client.py (raises KilnError),
         master/arbiter.py (maps failures to codes), config.py (ConfigError)

READ FIRST
    docs/spec/02-protocol.md §3.3 (the complete error-code list), docs/python/enum.md,
    docs/python/exceptions.md

MILESTONE
    Introduced in K1.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    """Every error code a KP/1 `error` reply can carry (docs/spec/02-protocol.md §3.3).

    Why:
        A closed list of names: the protocol spec says "add new ones here first", and the enum
        makes that the only place they can come from.
    """

    BAD_FRAME = "BAD_FRAME"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    BAD_REQUEST = "BAD_REQUEST"
    UNKNOWN_TASK = "UNKNOWN_TASK"
    NOT_FOUND = "NOT_FOUND"
    QUEUE_FULL = "QUEUE_FULL"
    NOT_CANCELLABLE = "NOT_CANCELLABLE"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    NOT_READY = "NOT_READY"
    DEBUG_DISABLED = "DEBUG_DISABLED"
    INTERNAL = "INTERNAL"


class KilnError(Exception):
    """Base class for every exception Kiln raises on purpose.

    Why:
        Lets callers distinguish "Kiln reported a problem" (KilnError and its subclasses)
        from genuine bugs (any other exception). A KilnError may carry an ErrorCode, so the
        CLI can show the same code the server sent.
    """

    # PY: __init__ is the method that sets up a new object; see docs/python/classes-and-objects.md.
    def __init__(self, message: str, code: ErrorCode | None = None) -> None:
        """Store the message (shown to users) and an optional protocol error code.

        Args:
            message: what went wrong and, ideally, how to fix it.
            code: the KP/1 error code, when the error came from (or maps to) the protocol.
        """
        # PY: super().__init__(message) lets the built-in Exception store the message, so
        # str(error) and tracebacks show it.
        super().__init__(message)
        self.code = code
        self.message = message
