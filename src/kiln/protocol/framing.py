r"""Length-prefixed framing: turn a stream of bytes into whole JSON messages, and back.

WHY THIS MODULE EXISTS
    TCP and Unix sockets deliver a *stream* of bytes with no message boundaries: one recv()
    may return half a message, or two and a half (docs/concepts/framing.md). KP/1 marks the
    boundaries by sending each message's length first, as 4 big-endian bytes
    (docs/spec/02-protocol.md §2, ADR-008). This module is the only code that knows that.

HOW IT FITS
    sender:   dict ──encode_frame()──▶ b"\x00\x00\x00\x0b" + b'{"type":"x"}' ──▶ socket
    receiver: socket ──bytes, in any chunks──▶ FrameDecoder.feed() ──▶ [dict, dict, ...]
    Used by master/connections.py, worker/main.py and client.py.

READ FIRST
    docs/concepts/framing.md, docs/python/struct.md, docs/python/json.md,
    docs/python/bytearray-and-memoryview.md

MILESTONE
    Introduced in K1.
"""

import json
import struct

from kiln.errors import KilnError

# PY: struct.Struct("!I") describes a binary layout: "!" = network byte order (big-endian),
# "I" = one unsigned 32-bit integer. HEADER.pack(5) gives b"\x00\x00\x00\x05".
# See docs/python/struct.md.
HEADER = struct.Struct("!I")
DEFAULT_MAX_FRAME_BYTES = 1_048_576  # 1 MiB (protocol.max_frame_bytes, docs/spec/05 §2)


class FrameError(KilnError):
    """Base class for "these bytes are not a valid KP/1 frame".

    Why:
        Callers that only care "is this connection broken?" catch FrameError; the two
        subclasses say exactly why (spec/02 §2.1 treats them slightly differently).
    """


class FrameTooLarge(FrameError):
    """The length prefix announced more bytes than the allowed maximum.

    Why:
        Raised as soon as the 4 header bytes arrive, before reading the body, so a peer can't
        make us buffer gigabytes by lying about the length.
    """


class BadFrame(FrameError):
    """The frame is empty, not UTF-8, not JSON, contains NaN/Infinity, or isn't an object.

    Why:
        spec/02 §2 requires every payload to be a JSON *object*; anything else is a protocol
        violation the receiver must report and close on.
    """


def _reject_constant(name: str) -> object:
    """Refuse the non-standard JSON constants NaN, Infinity and -Infinity.

    Why:
        Python's json module accepts these by default, but they aren't valid JSON, and other
        languages' parsers reject them (spec/02 §2, `allow_nan=False`).

    Args:
        name: the constant json.loads found.

    Raises:
        BadFrame: always.
    """
    raise BadFrame(f"JSON constant {name} is not allowed")


def encode_frame(obj: dict[str, object], max_bytes: int = DEFAULT_MAX_FRAME_BYTES) -> bytes:
    r"""Turn one message into the bytes to send: a 4-byte length, then UTF-8 JSON.

    Why:
        The sender checks the size too, so we never send a frame the receiver is guaranteed
        to reject (it would close the connection).

    Args:
        obj: the message, a dict of JSON-compatible values.
        max_bytes: the largest payload allowed.

    Returns:
        The frame bytes.

    Raises:
        FrameTooLarge: if the encoded payload is larger than max_bytes.
        ValueError: if obj contains NaN/Infinity or something json can't encode.

    Example:
        >>> encode_frame({"type": "ping"})
        b'\x00\x00\x00\x0f{"type":"ping"}'
    """
    # WHY: compact separators (no spaces) and ensure_ascii=False (send "é" as 2 UTF-8 bytes,
    # not the 6 characters "é"): both are spec/02 §2 rules.
    payload = json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    body = payload.encode("utf-8")
    if len(body) > max_bytes:
        raise FrameTooLarge(f"frame of {len(body)} bytes exceeds the {max_bytes}-byte limit")
    return HEADER.pack(len(body)) + body


class FrameDecoder:
    """Collect incoming bytes and hand back each complete frame exactly once.

    Why:
        The receiver never knows how the bytes were chunked on the way. The decoder keeps a
        buffer of everything not yet used, and only returns a message when *all* of its bytes
        are present. Feeding it one byte at a time or a hundred frames at once gives the same
        messages (docs/spec/02-protocol.md §2.2, tested in tests/unit/test_framing.py).
    """

    def __init__(self, max_bytes: int = DEFAULT_MAX_FRAME_BYTES) -> None:
        """Create an empty decoder.

        Args:
            max_bytes: the largest payload to accept.
        """
        self.max_bytes = max_bytes
        # PY: a bytearray is a *changeable* sequence of bytes: we can append to it and delete
        # from its front. See docs/python/bytearray-and-memoryview.md.
        self._buffer = bytearray()

    @property
    def buffered(self) -> int:
        """How many received bytes are waiting to become a complete frame.

        Returns:
            The buffer length.
        """
        return len(self._buffer)

    def feed(self, data: bytes) -> list[dict[str, object]]:
        """Add newly received bytes and return every frame that is now complete.

        Why:
            Called after every recv(). Returns 0, 1 or many messages; leftover bytes (a
            partial frame) stay in the buffer for the next call.

        Args:
            data: the bytes a recv() just returned (any length, even 1).

        Returns:
            The complete messages, in order.

        Raises:
            FrameTooLarge: a length prefix is over the limit.
            BadFrame: a complete frame's payload is invalid.
        """
        self._buffer += data
        messages: list[dict[str, object]] = []
        # WHY: loop because one chunk of bytes may contain several complete frames.
        while len(self._buffer) >= HEADER.size:
            # PY: unpack_from reads the 4-byte header from the start of the buffer without
            # copying; it returns a tuple, so (length,) takes its only item.
            (length,) = HEADER.unpack_from(self._buffer, 0)
            if length == 0:
                raise BadFrame("zero-length frame")
            if length > self.max_bytes:
                # WHY: fail on the header alone (spec/02 §2.2): never wait for a huge body.
                raise FrameTooLarge(f"peer announced {length} bytes (limit {self.max_bytes})")
            end = HEADER.size + length
            if len(self._buffer) < end:
                break  # WHY: the body hasn't fully arrived yet; wait for more bytes.
            body = bytes(self._buffer[HEADER.size : end])
            # WHY: deleting from the front of a bytearray moves the remaining bytes down. That
            # costs time proportional to what's left, which is fine for frames of a few KiB
            # (the code tour discusses the trade-off).
            del self._buffer[:end]
            messages.append(_decode_body(body))
        return messages


def _decode_body(body: bytes) -> dict[str, object]:
    """Turn one frame's payload bytes into a dict, enforcing spec/02 §2's rules.

    Why:
        Every way a payload can be wrong becomes the same BadFrame error, which callers
        answer with BAD_FRAME and a closed connection.

    Args:
        body: exactly one frame's payload.

    Returns:
        The decoded JSON object.

    Raises:
        BadFrame: invalid UTF-8, invalid JSON, a NaN/Infinity constant, or not an object.
    """
    try:
        text = body.decode("utf-8")
        value = json.loads(text, parse_constant=_reject_constant)
    except UnicodeDecodeError as error:
        raise BadFrame(f"payload is not valid UTF-8: {error}") from error
    except json.JSONDecodeError as error:
        raise BadFrame(f"payload is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise BadFrame(f"payload must be a JSON object, got {type(value).__name__}")
    return value
