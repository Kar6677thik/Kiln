"""Tests for KP/1 framing: the four behaviours of docs/spec/02-protocol.md §2.2, and more."""

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kiln.protocol.framing import HEADER, BadFrame, FrameDecoder, FrameTooLarge, encode_frame

# A strategy for small JSON objects: what real messages look like.
json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=20),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=8), children, max_size=4)
    ),
    max_leaves=10,
)
messages = st.dictionaries(st.text(min_size=1, max_size=8), json_values, min_size=1, max_size=5)


def test_one_byte_at_a_time_yields_the_frame_once_on_the_last_byte() -> None:
    """Feeding a frame byte by byte returns it exactly once, on the final byte (spec/02 §2.2)."""
    frame = encode_frame({"type": "ping", "req_id": 1})
    decoder = FrameDecoder()
    results = [decoder.feed(frame[i : i + 1]) for i in range(len(frame))]
    assert all(r == [] for r in results[:-1])
    assert results[-1] == [{"type": "ping", "req_id": 1}]


def test_three_concatenated_frames_decode_in_order() -> None:
    """Three frames arriving in one recv() all come out, in order (spec/02 §2.2)."""
    data = b"".join(encode_frame({"n": n}) for n in range(3))
    assert FrameDecoder().feed(data) == [{"n": 0}, {"n": 1}, {"n": 2}]


@given(messages, st.data())
def test_split_at_any_offset_decodes_identically(
    message: dict[str, object], data: st.DataObject
) -> None:
    """However a frame is cut into chunks, the decoder produces the same message (Hypothesis).

    Property-based: Hypothesis invents messages and cut points, and shrinks any failure to the
    smallest example (docs/python/hypothesis-basics.md).
    """
    frame = encode_frame(message)
    cut = data.draw(st.integers(min_value=0, max_value=len(frame)))
    decoder = FrameDecoder()
    assert decoder.feed(frame[:cut]) + decoder.feed(frame[cut:]) == [message]
    assert decoder.buffered == 0


@given(
    st.lists(messages, min_size=1, max_size=5),
    st.lists(st.integers(min_value=1, max_value=7), min_size=1),
)
def test_random_chunking_of_many_frames(batch: list[dict[str, object]], sizes: list[int]) -> None:
    """Many frames, fed in random-sized chunks, come out complete and in order."""
    stream = b"".join(encode_frame(m) for m in batch)
    decoder = FrameDecoder()
    out: list[dict[str, object]] = []
    position = 0
    index = 0
    while position < len(stream):
        size = sizes[index % len(sizes)]
        out += decoder.feed(stream[position : position + size])
        position += size
        index += 1
    assert out == batch


def test_oversize_length_fails_as_soon_as_the_header_arrives() -> None:
    """An announced length over the limit raises immediately, before any body bytes arrive."""
    decoder = FrameDecoder(max_bytes=100)
    with pytest.raises(FrameTooLarge):
        decoder.feed(HEADER.pack(101))


@pytest.mark.parametrize(
    "body",
    [b"\xff\xfe", b"not json", b"[1, 2]", b"NaN", b'{"x": NaN}', b'"text"'],
    ids=["bad-utf8", "bad-json", "array", "nan", "nan-in-object", "string"],
)
def test_invalid_payloads_are_bad_frames(body: bytes) -> None:
    """Payloads that aren't a UTF-8 JSON *object* (or contain NaN) are rejected (spec/02 §2)."""
    with pytest.raises(BadFrame):
        FrameDecoder().feed(HEADER.pack(len(body)) + body)


def test_zero_length_frame_is_bad() -> None:
    """A zero-length frame is a protocol violation (spec/02 §2)."""
    with pytest.raises(BadFrame):
        FrameDecoder().feed(HEADER.pack(0))


def test_encoder_refuses_to_send_what_the_peer_would_reject() -> None:
    """encode_frame checks the size too, so we never send a frame the receiver must reject."""
    with pytest.raises(FrameTooLarge):
        encode_frame({"x": "a" * 200}, max_bytes=100)


def test_encoding_is_compact_utf8_json() -> None:
    """Frames use compact separators and raw UTF-8 (é is 2 bytes, not an escape sequence)."""
    frame = encode_frame({"word": "café"})
    body = frame[HEADER.size :]
    assert body == '{"word":"café"}'.encode()
    assert HEADER.unpack(frame[: HEADER.size]) == (len(body),)
    assert json.loads(body) == {"word": "café"}
