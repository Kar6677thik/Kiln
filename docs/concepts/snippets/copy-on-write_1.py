# requires: linux
"""Copy-on-write after fork, and why *reading* Python objects copies pages.

The parent builds two things before forking: 64 MiB of raw bytes, and a list of 300,000
small Python objects. The child measures its own Private_Dirty (pages it has had to copy)
after each step:
  1. right after fork            → almost nothing private: everything is shared
  2. after READING the raw bytes → still nothing: reading bytes doesn't write
  3. after WRITING 16 MiB of them → +16 MiB: each written page was copied
  4. after READING every object   → many MiB: reading a Python object writes its refcount
"""

import os


def private_dirty_mib() -> float:
    """This process's Private_Dirty from /proc/self/smaps_rollup, in MiB."""
    for line in open("/proc/self/smaps_rollup"):
        if line.startswith("Private_Dirty:"):
            return int(line.split()[1]) / 1024
    return 0.0


raw = bytearray(os.urandom(64 * 1024 * 1024))  # 64 MiB of real (non-zero) pages
objects = [str(i) * 3 for i in range(300_000)]  # 300,000 separate str objects

pid = os.fork()
if pid == 0:
    start = private_dirty_mib()
    print(
        f"child: private dirty right after fork        +{private_dirty_mib() - start:5.1f} MiB",
        flush=True,
    )
    total = sum(raw[i] for i in range(0, len(raw), 4096))  # read one byte per page
    print(
        f"child: after reading all 64 MiB of bytes     +{private_dirty_mib() - start:5.1f} MiB",
        flush=True,
    )
    for i in range(0, 16 * 1024 * 1024, 4096):  # write one byte per page in 16 MiB
        raw[i] = 0
    print(
        f"child: after writing 16 MiB of bytes         +{private_dirty_mib() - start:5.1f} MiB",
        flush=True,
    )
    after_write = private_dirty_mib()
    length = sum(len(s) for s in objects)  # only READS the strings... or does it?
    print(
        f"child: after reading 300,000 str objects     +{private_dirty_mib() - after_write:5.1f} MiB more",
        flush=True,
    )
    os._exit(0)
os.waitpid(pid, 0)
