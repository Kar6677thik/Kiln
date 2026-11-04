# requires: linux
"""Virtual size vs resident memory: reserving memory is not the same as using it."""

import mmap


def status(field):
    with open("/proc/self/status", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith(field + ":"):
                return int(line.split()[1]) // 1024  # kB -> MiB


def show(label):
    print(
        f"{label:<34} VmSize={status('VmSize'):>5} MiB  VmRSS={status('VmRSS'):>4} MiB  VmHWM={status('VmHWM'):>4} MiB"
    )


show("start")
area = mmap.mmap(-1, 200 * 1024 * 1024)  # reserve 200 MiB of anonymous memory
show("after reserving 200 MiB")
for offset in range(0, 100 * 1024 * 1024, 4096):  # touch one byte per 4 KiB page, first 100 MiB
    area[offset] = 1
show("after touching 100 MiB of it")
area.close()
show("after releasing it")
