"""Turn Unix signals into ordinary events for the master's loop (the wakeup-fd trick).

WHY THIS MODULE EXISTS
    A signal (Ctrl-C, `kill -HUP`, a child dying) can arrive at *any* moment, even halfway
    through the loop updating the job table. Python runs signal handlers between two bytecodes
    of whatever the main thread is doing, so doing real work inside a handler could corrupt
    state. Instead, the handler only *records* the signal, and Python's C-level handler writes
    a byte to a socket we watch with the selector. The loop wakes up, reads the byte, and
    handles the signal at a safe point (docs/spec/04-supervisor.md §3, docs/concepts/signals.md).

HOW IT FITS
    kernel ──SIGHUP──▶ Python C handler ──1 byte──▶ wakeup socket ──readable──▶ arbiter loop
                       └──▶ our handler: pending.append(SIGHUP)   arbiter: drain() → reload()

READ FIRST
    docs/concepts/signals.md, docs/python/signal-module.md, docs/python/socket-module.md

MILESTONE
    Introduced in K1.
"""

import signal
import socket
from types import FrameType

# The signals the master reacts to, and what each means (spec/04 §3).
MASTER_SIGNALS: dict[signal.Signals, str] = {
    signal.SIGINT: "graceful_or_fast_shutdown",
    signal.SIGTERM: "graceful_shutdown",
    signal.SIGQUIT: "immediate_shutdown",
    signal.SIGHUP: "reload",
    signal.SIGTTIN: "scale_up",
    signal.SIGTTOU: "scale_down",
    signal.SIGUSR1: "status_dump",
    signal.SIGCHLD: "reap",
}


class SignalPipe:
    """Owns the wakeup socketpair and the list of signals not yet handled.

    Why:
        Bundles setup (install handlers, register the wakeup fd) with teardown (restore
        everything), and keeps the handler itself trivially small.
    """

    def __init__(self) -> None:
        """Create the socketpair and install the handlers.

        Why:
            Handlers and the wakeup fd must be in place before the first fork, so this happens in
            the constructor.
        """
        # WHY: a socketpair is two connected sockets; signal.set_wakeup_fd writes into one end
        # (the "write" socket) and the selector watches the other end (the "read" socket).
        self.read_sock, self.write_sock = socket.socketpair()
        self.read_sock.setblocking(False)
        # WHY: the C handler must never block inside a signal; a full buffer just drops bytes,
        # which is fine because `pending` (not the bytes) is the real record.
        self.write_sock.setblocking(False)
        self.pending: list[int] = []
        self._previous: dict[signal.Signals, object] = {}
        self._previous_wakeup = signal.set_wakeup_fd(
            self.write_sock.fileno(), warn_on_full_buffer=False
        )
        for signum in MASTER_SIGNALS:
            self._previous[signum] = signal.signal(signum, self._handler)

    def _handler(self, signum: int, frame: FrameType | None) -> None:
        """The Python-level handler: record the signal, nothing else.

        Why:
            Runs "between two bytecodes" of the main thread (docs/concepts/signals.md). It must
            never touch the job table or sockets: the loop does that later.

        Args:
            signum: the signal number.
            frame: the interrupted stack frame (unused).
        """
        self.pending.append(signum)

    def fileno(self) -> int:
        """The fd the selector watches.

        Returns:
            The read end's fd.
        """
        return self.read_sock.fileno()

    def drain(self) -> list[signal.Signals]:
        """Discard the wakeup bytes and return the recorded signals, oldest first.

        Why:
            Empties the wakeup pipe (so select stops waking) and hands the loop the signals to act
            on, in order.

        Returns:
            The signals that arrived since the last drain.
        """
        while True:
            try:
                if not self.read_sock.recv(4096):
                    break
            except BlockingIOError:
                break
        # WHY: swap the list out in one step: a signal arriving right now lands in the new
        # list and is handled on the next pass, never lost.
        arrived, self.pending = self.pending, []
        return [signal.Signals(signum) for signum in arrived]

    def fds(self) -> list[int]:
        """Both fds, so forked workers can close them (spec/04 §6 rule 1).

        Returns:
            The two fd numbers.
        """
        return [self.read_sock.fileno(), self.write_sock.fileno()]

    def restore(self) -> None:
        """Put back the previous handlers and wakeup fd, and close the sockets."""
        signal.set_wakeup_fd(self._previous_wakeup)
        for signum, previous in self._previous.items():
            signal.signal(signum, previous)  # type: ignore[arg-type]  # WHY: previous came from signal.signal()
        self.read_sock.close()
        self.write_sock.close()
