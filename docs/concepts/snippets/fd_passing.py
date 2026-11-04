# requires: linux
"""Gate G2.3: pass an open file descriptor to another process over a Unix socketpair.

The parent opens a file, then sends the *open file* (not its name) to a forked child with
socket.send_fds. The child receives a working fd, usually with a different number.
"""

import os
import socket
import tempfile

with tempfile.NamedTemporaryFile("w", delete=False) as handle:
    handle.write("hello from a file the child never opened\n")
    path = handle.name

parent_end, child_end = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
pid = os.fork()
if pid == 0:
    parent_end.close()
    # Open a few files first, so the fd numbers in the child are clearly different.
    padding = [os.open("/dev/null", os.O_RDONLY) for _ in range(3)]
    data, fds, flags, address = socket.recv_fds(child_end, 1024, 1)
    received = fds[0]
    print(f"child : got message {data!r} and fd {received}", flush=True)
    os.lseek(received, 0, os.SEEK_SET)
    print(f"child : reading it gives {os.read(received, 100)!r}", flush=True)
    os._exit(0)

child_end.close()
fd = os.open(path, os.O_RDONLY)
print(f"parent: opened the file as fd {fd}", flush=True)
socket.send_fds(parent_end, [b"here is a file"], [fd])
os.close(fd)
os.waitpid(pid, 0)
os.unlink(path)
