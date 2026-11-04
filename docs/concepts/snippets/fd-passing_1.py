# requires: linux
"""PEP 446: fds are NOT inherited by a new program unless you mark them inheritable."""

import os
import sys

read_fd, write_fd = os.pipe()
print(f"pipe fds {read_fd}, {write_fd}; inheritable by default? {os.get_inheritable(write_fd)}")

CHILD = "import os, sys\ntry:\n    os.write(int(sys.argv[1]), b'x')\n    print('  child: fd', sys.argv[1], 'works')\nexcept OSError as e:\n    print('  child: fd', sys.argv[1], 'fails:', e.strerror)\n"

for inheritable in (False, True):
    os.set_inheritable(write_fd, inheritable)
    print(f"spawning a fresh python with the fd marked inheritable={inheritable}:", flush=True)
    pid = os.posix_spawn(sys.executable, [sys.executable, "-c", CHILD, str(write_fd)], os.environ)
    os.waitpid(pid, 0)
