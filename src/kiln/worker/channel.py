"""The worker's end of its socketpair: thread-safe sending, blocking receiving.

WHY THIS MODULE EXISTS
    Two threads in a worker send on the same socket: the main thread (results) and the
    heartbeat thread (heartbeats). If both called sendall() at once, the bytes of two frames
    could interleave and the master would see garbage. A lock makes each frame go out whole
    (K1 spec §5.8, a race the code tour points out).

HOW IT FITS
    worker/main.py ──channel.send(msg)──┐
    worker/heartbeat.py ──channel.send(msg)──┴─▶ Lock ──▶ sock.sendall(frame)
    worker/main.py ──channel.receive()──▶ blocking recv ──▶ FrameDecoder ──▶ messages

READ FIRST
    docs/python/threading-basics.md (Lock), docs/concepts/framing.md

MILESTONE
    Introduced in K1.
"""

import socket
import threading

from kiln.protocol.framing import FrameDecoder, encode_frame


class ChannelClosed(Exception):
    """The master closed its end (it exited or crashed).

    Why:
        Workers don't outlive their master (K1 spec §5.8 step 9): this ends the worker.
    """


class Channel:
    """A blocking socket to the master, with a send lock.

    Why:
        Workers can block, unlike the master; only *sending* needs care, because of the
        heartbeat thread.
    """

    def __init__(self, sock: socket.socket, max_frame: int) -> None:
        """Wrap the worker's end of the socketpair.

        Why:
            Workers may block, unlike the master, so the socket is switched to blocking mode once,
            here.

        Args:
            sock: the socket (set to blocking here).
            max_frame: protocol.max_frame_bytes.
        """
        sock.setblocking(True)
        self.sock = sock
        self.max_frame = max_frame
        self.decoder = FrameDecoder(max_frame)
        self._lock = threading.Lock()
        self._ready: list[dict[str, object]] = []

    def send(self, message: dict[str, object]) -> None:
        """Send one message, whole, even if another thread is sending too.

        Why:
            The heartbeat thread sends too; without the lock two frames could interleave their
            bytes.

        Args:
            message: the message dict.

        Raises:
            ChannelClosed: the master is gone.
        """
        frame = encode_frame(message, self.max_frame)
        # PY: "with lock:" takes the lock and always releases it, even on an error.
        with self._lock:
            try:
                self.sock.sendall(frame)
            except OSError as error:
                raise ChannelClosed(str(error)) from error

    def receive(self, timeout_s: float | None = None) -> dict[str, object] | None:
        """Wait for the next message from the master.

        Why:
            One recv may bring several frames; extras are kept so each call returns exactly one
            message.

        Args:
            timeout_s: give up after this long and return None (None = wait forever).

        Returns:
            The next message, or None on timeout.

        Raises:
            ChannelClosed: the master closed the connection.
        """
        while not self._ready:
            self.sock.settimeout(timeout_s)
            try:
                data = self.sock.recv(65536)
            except TimeoutError:
                return None
            except OSError as error:
                raise ChannelClosed(str(error)) from error
            if data == b"":
                raise ChannelClosed("master closed the channel")
            self._ready.extend(self.decoder.feed(data))
        return self._ready.pop(0)

    def close(self) -> None:
        """Close the socket."""
        self.sock.close()
