# requires: linux
"""Which cgroup am I in, and what does its memory.events file say?"""

from pathlib import Path

line = Path("/proc/self/cgroup").read_text().strip()
print("/proc/self/cgroup:", line)
group = line.split("::", 1)[1]  # cgroup v2: a single line "0::/some/path"
events = Path("/sys/fs/cgroup") / group.lstrip("/") / "memory.events"
print("memory.events path:", events)
if events.exists():
    print(events.read_text().strip())
else:
    print("(no memory.events here: the memory controller isn't enabled for this group)")
