"""The heartbeat thread: tells the master "still alive" every heartbeat_interval_s.

WHY THIS MODULE EXISTS
    A worker running a long job can't answer messages, so silence alone doesn't mean it's
    hung. A small background thread sends a heartbeat every second regardless. Silence then
    *does* mean trouble: the process is stuck so badly that even this thread can't run (a C
    call holding the GIL, a deadlock), and the master kills it (spec/04 §5.4).

HOW IT FITS
    run_worker() ──start()──▶ HeartbeatThread ──every interval──▶ channel.send(heartbeat)
    master: no heartbeat for heartbeat_timeout_s ──▶ SIGKILL (reason heartbeat_timeout)

READ FIRST
    docs/python/threading-basics.md, docs/concepts/fork-and-threads.md (why a worker may
    start threads but the master may not)

MILESTONE
    Introduced in K1.
"""

import sys
import threading
from collections.abc import Callable

from kiln.memory.procfs import read_status
from kiln.protocol.messages import Heartbeat, MsgType, message
from kiln.worker.channel import Channel, ChannelClosed


def _gil_enabled() -> bool:
    """Report whether the GIL is on (always True on normal builds).

    Returns:
        The GIL state.
    """
    probe = getattr(sys, "_is_gil_enabled", None)
    return True if probe is None else bool(probe())


class HeartbeatThread(threading.Thread):
    """Sends a heartbeat message every interval until stopped.

    Why:
        A separate thread is the only way to keep talking while the main thread runs a task.
        daemon=True means it never keeps the worker alive on its own.
    """

    def __init__(
        self,
        channel: Channel,
        interval_s: float,
        progress: Callable[[], tuple[int, int]],
        on_tick: Callable[[], object] | None = None,
    ) -> None:
        """Prepare the thread (call start() to run it).

        Why:
            on_tick lets K3's gcwatch poll ride on a thread that already wakes regularly.

        Args:
            channel: the shared channel (its send is thread-safe).
            interval_s: worker.heartbeat_interval_s.
            progress: returns (jobs_done, inflight) for the heartbeat fields.
            on_tick: called once per interval (K3: the gcwatch poll, layer C(1)).
        """
        super().__init__(name="kiln-heartbeat", daemon=True)
        self.channel = channel
        self.interval_s = interval_s
        self.progress = progress
        self.on_tick = on_tick
        self._stop_event = threading.Event()

    def run(self) -> None:
        """The thread's body: send, wait one interval, repeat.

        Why:
            Event.wait(timeout) sleeps but wakes immediately when stop() is called, so the
            worker exits promptly.
        """
        while not self._stop_event.is_set():
            if self.on_tick is not None:
                self.on_tick()
            jobs_done, inflight = self.progress()
            try:
                rss = read_status("self").vm_rss
            except OSError:
                rss = 0
            beat = Heartbeat(
                rss_bytes=rss, jobs_done=jobs_done, inflight=inflight, gil_enabled=_gil_enabled()
            )
            try:
                self.channel.send(message(MsgType.HEARTBEAT, beat))
            except ChannelClosed:
                return  # WHY: the master is gone; the main thread will notice and exit.
            self._stop_event.wait(self.interval_s)

    def stop(self) -> None:
        """Ask the thread to finish after its current iteration."""
        self._stop_event.set()
