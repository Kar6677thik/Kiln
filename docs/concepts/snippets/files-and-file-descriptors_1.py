# requires: linux
# File descriptors are small integers the kernel gives a process for each open "file".
import os
import sys
import tempfile

print("stdin, stdout, stderr fds:", sys.stdin.fileno(), sys.stdout.fileno(), sys.stderr.fileno())

with tempfile.TemporaryDirectory() as folder:
    path = os.path.join(folder, "notes.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fd_number = fh.fileno()
        print("open() gave a file object; its fd is", fd_number)
        # /proc/self/fd has one entry per open fd; each is a link to what the fd refers to.
        for name in sorted(os.listdir("/proc/self/fd"), key=int):
            try:
                target = os.readlink(f"/proc/self/fd/{name}")
            except FileNotFoundError:
                target = "(closed while listing)"
            print(f"  fd {name:>2} -> {target}")
    print("after the with-block, is fd still open?", os.path.exists(f"/proc/self/fd/{fd_number}"))

# Writing straight to fd 1 (stdout) with the low-level os.write, bypassing print():
sys.stdout.flush()
os.write(1, b"written directly to file descriptor 1\n")
