# requires: linux
"""Read /proc/<pid>/smaps_rollup for a parent and a forked child that share 40 MiB.

Shows the RSS / PSS / USS arithmetic on a real 2-process example: the shared pages count
fully in each RSS, half in each PSS, and not at all in USS.
"""

import os
import time

FIELDS = ("Rss", "Pss", "Shared_Clean", "Shared_Dirty", "Private_Clean", "Private_Dirty")


def rollup(pid: int) -> dict[str, float]:
    """The interesting smaps_rollup lines of a process, in MiB."""
    values = {}
    for line in open(f"/proc/{pid}/smaps_rollup"):
        name, _, rest = line.partition(":")
        if name in FIELDS:
            values[name] = int(rest.split()[0]) / 1024  # the file says kB; it means KiB
    return values


def show(label: str, pid: int) -> dict[str, float]:
    """Print one process's numbers and return them."""
    v = rollup(pid)
    uss = v["Private_Clean"] + v["Private_Dirty"]
    shared = v["Shared_Clean"] + v["Shared_Dirty"]
    print(
        f"{label:6} Rss {v['Rss']:6.1f}  Pss {v['Pss']:6.1f}  USS {uss:6.1f}  Shared {shared:6.1f}  (MiB)"
    )
    return {"rss": v["Rss"], "pss": v["Pss"]}


data = bytearray(os.urandom(40 * 1024 * 1024))  # 40 MiB, written BEFORE the fork
reader, writer = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(reader)
    os.write(writer, b"x")  # "I'm alive"; then just sit there sharing the 40 MiB
    time.sleep(5)
    os._exit(0)
os.close(writer)
os.read(reader, 1)
print(
    "one raw 'Rss:' line:",
    next(line.strip() for line in open("/proc/self/smaps_rollup") if line.startswith("Rss:")),
)
p = show("parent", os.getpid())
c = show("child", pid)
print(f"sum of RSS = {p['rss'] + c['rss']:6.1f} MiB   sum of PSS = {p['pss'] + c['pss']:6.1f} MiB")
os.kill(pid, 9)
os.waitpid(pid, 0)
