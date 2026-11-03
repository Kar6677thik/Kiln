# requires: linux
# Where am I running? Prints facts that tell Windows and Linux apart, and
# whether the current folder lives on the slow 9P mount (/mnt/c) or on ext4.
import os
import platform
import sys


def filesystem_type(path):
    # /proc/mounts lists every mounted filesystem: "device mountpoint type options ...".
    # The longest mountpoint that is a prefix of `path` is the one that holds it.
    best_mount, best_type = "", "unknown"
    with open("/proc/mounts", encoding="utf-8") as mounts:
        for line in mounts:
            fields = line.split()
            mount_point, fs_type = fields[1], fields[2]
            inside = path == mount_point or path.startswith(mount_point.rstrip("/") + "/")
            if inside and len(mount_point) > len(best_mount):
                best_mount, best_type = mount_point, fs_type
    return best_mount, best_type


print("sys.platform     :", sys.platform)
print("kernel release   :", platform.release())
print("running in WSL?  :", "microsoft" in platform.release().lower())
print("home folder      :", os.path.expanduser("~"))
print("current folder   :", os.getcwd())
mount, fs = filesystem_type(os.getcwd())
print("current folder fs:", fs, "(mounted at", mount + ")")
mount, fs = filesystem_type(os.path.expanduser("~"))
print("home folder fs   :", fs, "(mounted at", mount + ")")
