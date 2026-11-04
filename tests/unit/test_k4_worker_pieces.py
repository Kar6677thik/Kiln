"""K4 pieces that don't need a server: the channel's send lock and the config gate."""

import socket
import sys
import threading

import pytest

from kiln.config import ConfigError, build_config
from kiln.protocol.framing import FrameDecoder
from kiln.worker.channel import Channel

pytestmark = pytest.mark.unit


def test_concurrent_senders_never_interleave_frames() -> None:
    """8 threads × 1,000 messages through one Channel: every frame decodes whole (K4 §5.2)."""
    ours, theirs = socket.socketpair()
    channel = Channel(ours, 1 << 20)
    received: list[dict[str, object]] = []

    def reader() -> None:
        decoder = FrameDecoder(1 << 20)
        while len(received) < 8000:
            data = theirs.recv(65536)
            if not data:
                return
            received.extend(decoder.feed(data))

    collector = threading.Thread(target=reader)
    collector.start()

    def sender(n: int) -> None:
        for i in range(1000):
            channel.send({"type": "heartbeat", "sender": n, "i": i, "pad": "x" * (i % 300)})

    senders = [threading.Thread(target=sender, args=(n,)) for n in range(8)]
    for thread in senders:
        thread.start()
    for thread in senders:
        thread.join()
    collector.join(timeout=20)
    ours.close()
    theirs.close()
    assert len(received) == 8000
    for n in range(8):
        mine = [m["i"] for m in received if m["sender"] == n]
        assert mine == list(range(1000))  # whole, and in each sender's order


def test_interpreters_executor_is_refused_on_3_13(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-4.8: worker.executor = interpreters fails validation on Python < 3.14."""
    monkeypatch.setattr(sys, "version_info", (3, 13, 17, "final", 0))
    with pytest.raises(ConfigError) as caught:
        build_config({"worker.executor": "interpreters"})
    assert (
        'worker.executor = "interpreters" needs Python ≥ 3.14 (this is 3.13.17). '
        'Use "threads" or a newer interpreter.'
    ) in caught.value.problems
