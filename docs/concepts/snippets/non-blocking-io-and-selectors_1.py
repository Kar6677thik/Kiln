# requires: linux
"""Blocking vs non-blocking reads, and a selector waiting on several sockets at once."""

import selectors
import socket

# Which selector does Python pick on this machine? (epoll on Linux)
print("DefaultSelector is", selectors.DefaultSelector.__name__)

# A connected pair of local sockets: what goes in one end comes out the other.
left, right = socket.socketpair()
right.setblocking(False)  # recv() must never wait

# 1) Nothing has been sent yet: a non-blocking recv raises instead of waiting.
try:
    right.recv(1024)
except BlockingIOError as error:
    print("recv with no data ->", type(error).__name__, f"(errno {error.errno}, EAGAIN)")

# 2) Ask a selector to tell us when 'right' (or a second pair) becomes readable.
other_left, other_right = socket.socketpair()
other_right.setblocking(False)
selector = selectors.DefaultSelector()
selector.register(right, selectors.EVENT_READ, data="pair A")
selector.register(other_right, selectors.EVENT_READ, data="pair B")

print("select(timeout=0.1) with nothing sent ->", selector.select(timeout=0.1))

other_left.sendall(b"hello from B")
for key, events in selector.select(timeout=1.0):
    print("ready:", key.data, "->", key.fileobj.recv(1024))

# 3) Writes can be partial too: fill the send buffer until the kernel says "full".
left.setblocking(False)
sent_total = 0
try:
    while True:
        sent_total += left.send(b"x" * 65536)
except BlockingIOError:
    print(f"send buffer full after {sent_total // 1024} KiB: send() raised BlockingIOError")

for sock in (left, right, other_left, other_right):
    sock.close()
selector.close()
