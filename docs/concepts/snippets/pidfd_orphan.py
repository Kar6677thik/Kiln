# requires: linux
"""Gate G2.4: watch a process that is NOT our child with a pidfd.

We fork a middle process, which forks a grandchild and exits at once. The grandchild is
re-parented (it is no longer our child, so waitpid can't see it). A pidfd still tells us when
it exits: the fd becomes readable.
"""

import os
import select
import time

read_fd, write_fd = os.pipe()
middle = os.fork()
if middle == 0:
    grandchild = os.fork()
    if grandchild == 0:
        time.sleep(0.5)  # the grandchild lives a little while, then exits
        os._exit(7)
    os.write(write_fd, str(grandchild).encode())
    os._exit(0)  # the middle process exits right away: the grandchild is now an orphan

os.close(write_fd)
grandchild = int(os.read(read_fd, 32))
os.waitpid(middle, 0)
pidfd = os.pidfd_open(grandchild)
new_parent = int(open(f"/proc/{grandchild}/status").read().split("PPid:")[1].split()[0])
parent_name = open(f"/proc/{new_parent}/comm").read().strip()
print(f"grandchild pid {grandchild} is an orphan now", flush=True)
print(f"its new parent is pid {new_parent} ({parent_name}), not us (pid {os.getpid()})", flush=True)
try:
    os.waitpid(grandchild, os.WNOHANG)
except ChildProcessError as error:
    print(f"waitpid on it fails: ChildProcessError ({error.strerror})", flush=True)
started = time.monotonic()
ready, _, _ = select.select([pidfd], [], [], 5.0)
elapsed = time.monotonic() - started
print(f"pidfd readable: {bool(ready)} after about {round(elapsed, 1)} s", flush=True)
os.close(pidfd)
