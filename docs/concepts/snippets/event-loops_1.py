"""A tiny hand-written event loop: one thread, a selector, and a heap of timers."""

import heapq
import selectors
import socket
import time

selector = selectors.DefaultSelector()
timers = []  # a min-heap of (deadline, name): the smallest deadline is always timers[0]
start = time.monotonic()


def log(text):
    print(f"t={time.monotonic() - start:4.2f}s  {text}")


reader, writer = socket.socketpair()
reader.setblocking(False)
selector.register(reader, selectors.EVENT_READ)

heapq.heappush(timers, (start + 0.30, "send a message"))
heapq.heappush(timers, (start + 0.10, "say hello"))
heapq.heappush(timers, (start + 0.50, "stop"))

running = True
while running:
    # 1. How long may we sleep? Until the nearest timer, never longer.
    timeout = max(0.0, timers[0][0] - time.monotonic()) if timers else None
    # 2. Sleep until a socket is ready OR the timeout passes.
    for key, _ in selector.select(timeout):
        log(f"socket readable: {key.fileobj.recv(100)!r}")
    # 3. Run every timer whose deadline has passed.
    while timers and timers[0][0] <= time.monotonic():
        _, name = heapq.heappop(timers)
        log(f"timer fired: {name}")
        if name == "send a message":
            writer.send(b"ping")
        elif name == "stop":
            running = False

selector.close()
reader.close()
writer.close()
