# requires: linux
"""Pages are handed out lazily: reserving memory costs nothing until each page is touched.

We reserve 64 MiB with mmap, then touch one byte in half of its pages, and count the page
faults the kernel reports for us (resource.getrusage: ru_minflt = "minor" faults, the ones
that didn't need the disk).
"""

import mmap
import os
import resource

PAGE = os.sysconf("SC_PAGE_SIZE")
SIZE = 64 * 1024 * 1024


def faults() -> int:
    """Minor page faults this process has taken so far."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_minflt


def rss_mib() -> float:
    """Resident memory (VmRSS) in MiB, from /proc/self/status."""
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return 0.0


print(f"page size: {PAGE} bytes; 64 MiB = {SIZE // PAGE} pages")
before_rss, before = rss_mib(), faults()
area = mmap.mmap(-1, SIZE)  # anonymous memory: reserved, not yet backed by RAM
print(
    f"after reserving 64 MiB: page faults +{faults() - before}, RSS +{rss_mib() - before_rss:.0f} MiB"
)
before = faults()
for offset in range(0, SIZE // 2, PAGE):  # touch one byte in each page of the first half
    area[offset] = 1
touched = faults() - before
print(
    f"after touching 32 MiB ({SIZE // 2 // PAGE} pages): page faults +{touched}, RSS +{rss_mib() - before_rss:.0f} MiB"
)
area.close()
