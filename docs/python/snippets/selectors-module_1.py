# One thread watching three "connections" at once with a selector.
import selectors
import socket

sel = selectors.DefaultSelector()
pairs = []
for name in ("client-1", "worker-0", "worker-1"):
    ours, theirs = socket.socketpair()
    ours.setblocking(False)
    sel.register(ours, selectors.EVENT_READ, data=name)
    pairs.append((name, ours, theirs))

# Only two of the three peers send something.
pairs[0][2].send(b"submit")
pairs[2][2].send(b"result")

ready = sel.select(timeout=1)
names = sorted(key.data for key, mask in ready)
for key, mask in ready:
    key.fileobj.recv(100)
print("watched:", [name for name, ours, theirs in pairs])
print("readable:", names)

for name, ours, theirs in pairs:
    sel.unregister(ours)
    ours.close()
    theirs.close()
sel.close()
