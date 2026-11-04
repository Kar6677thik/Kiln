"""Non-blocking sockets with input and output buffers: the master's view of one connection.

WHY THIS MODULE EXISTS
    The master talks to many clients and workers from ONE thread, so it can never wait for a
    single socket (ADR-006). Every socket is non-blocking: recv() returns whatever bytes are
    there *now* (maybe half a message), and send() may accept only part of what we give it.
    A Connection hides both problems: incoming bytes go through a FrameDecoder, and outgoing
    bytes wait in an output buffer until the kernel takes them
    (docs/concepts/non-blocking-io-and-selectors.md, docs/spec/01-architecture.md §4 rules 1–3).

HOW IT FITS
    arbiter loop: socket readable ──conn.read()──▶ [message dicts]
                  conn.send(msg) ──▶ out buffer ──(socket writable)──▶ conn.flush()
    Used for client connections and worker channels alike.

READ FIRST
    docs/concepts/non-blocking-io-and-selectors.md, docs/concepts/framing.md,
    docs/python/socket-module.md

MILESTONE
    Introduced in K1.
"""

import socket

from kiln.protocol.framing import FrameDecoder, encode_frame

# WHY (ADR-031): limits that protect the master from slow or abusive clients. Constants, not
# config: nobody needs to tune them for the labs.
MAX_CLIENT_OUTBUF = 8 * 1024 * 1024  # a client that doesn't read its replies gets dropped
ACCEPT_BATCH = 64  # the most accept() calls per loop pass, so one burst can't starve the rest
MAX_CLIENTS = 1024  # beyond this, new connections are accepted and closed immediately
RECV_SIZE = 65536


class PeerClosed(Exception):
    """The other end closed the connection (recv returned b"" or the socket broke).

    Why:
        A normal event for clients, and for a worker channel it means the worker died.
    """


class Connection:
    """One non-blocking socket, its frame decoder and its pending output.

    Why:
        Keeps the "partial read / partial write" bookkeeping in one place, so the arbiter only
        deals in whole messages.
    """

    def __init__(self, sock: socket.socket, role: str, max_frame: int) -> None:
        """Wrap a socket (it is switched to non-blocking here).

        Why:
            Every master socket must be non-blocking, so the switch happens once, at the moment a
            socket is wrapped.

        Args:
            sock: a connected socket (accepted client, or the master's end of a socketpair).
            role: "client" or "worker" (used in limits and logs).
            max_frame: protocol.max_frame_bytes.
        """
        # WHY: non-blocking so one slow peer can never freeze the master (ADR-006).
        sock.setblocking(False)
        self.sock = sock
        self.role = role
        self.max_frame = max_frame
        self.decoder = FrameDecoder(max_frame)
        self.outbuf = bytearray()
        self.closed = False
        # WHY: set by the arbiter after a failing frame, so the error reply is flushed first
        # and then the connection is closed (spec/02 §2.1).
        self.close_after_flush = False

    def fileno(self) -> int:
        """Return the socket's file descriptor (selectors accept any object that has one).

        Returns:
            The fd number.
        """
        return self.sock.fileno()

    def read(self) -> list[dict[str, object]]:
        """Read whatever is available now and return the complete messages.

        Why:
            A readable socket may hold several messages, half a message, or a close; this turns all
            three into one result.

        Returns:
            Zero or more messages.

        Raises:
            PeerClosed: the peer closed the connection or it broke.
            FrameError: the peer sent invalid bytes (the caller replies BAD_FRAME and closes).
        """
        messages: list[dict[str, object]] = []
        # WHY: read until the kernel says "nothing more right now" (BlockingIOError); a
        # readable socket may hold more than one recv() worth of bytes.
        while True:
            try:
                data = self.sock.recv(RECV_SIZE)
            except BlockingIOError:
                # SYSCALL: recv(2) returned EAGAIN: no more data for now.
                return messages
            except (ConnectionResetError, OSError) as error:
                raise PeerClosed(str(error)) from error
            if data == b"":
                # WHY: recv() returning zero bytes is how a socket says "the peer closed".
                if messages:
                    return messages
                raise PeerClosed("peer closed the connection")
            messages.extend(self.decoder.feed(data))

    def send(self, message: dict[str, object]) -> None:
        """Queue a message for sending; flush() actually writes it.

        Args:
            message: the message dict.
        """
        self.outbuf += encode_frame(message, self.max_frame)

    def flush(self) -> bool:
        """Send as much of the output buffer as the kernel accepts right now.

        Why:
            send() on a non-blocking socket may write only part of the buffer (the kernel's
            socket buffer is full). We keep the rest and try again when the selector says the
            socket is writable (spec/01 §4 rule 3).

        Returns:
            True when everything has been sent.

        Raises:
            PeerClosed: the peer is gone.
        """
        while self.outbuf:
            try:
                sent = self.sock.send(self.outbuf)
            except BlockingIOError:
                return False  # SYSCALL: send(2) returned EAGAIN: kernel buffer full, wait
            except (BrokenPipeError, ConnectionResetError, OSError) as error:
                raise PeerClosed(str(error)) from error
            del self.outbuf[:sent]
        return True

    @property
    def wants_write(self) -> bool:
        """Whether there is unsent output (so the loop should watch for EVENT_WRITE).

        Returns:
            True if the output buffer is non-empty.
        """
        return bool(self.outbuf)

    @property
    def overflowing(self) -> bool:
        """Whether a *client* has let too much unsent output pile up (it isn't reading).

        Returns:
            True if over MAX_CLIENT_OUTBUF for a client connection.
        """
        return self.role == "client" and len(self.outbuf) > MAX_CLIENT_OUTBUF

    def close(self) -> None:
        """Close the socket (idempotent: safe to call twice, code-style §8)."""
        if not self.closed:
            self.closed = True
            self.sock.close()
