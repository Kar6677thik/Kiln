# requires: linux
# Reading Linux's /proc and /sys "files" with pathlib, the way kiln doctor does.
from pathlib import Path

proc = Path("/proc")
meminfo = (proc / "meminfo").read_text(encoding="utf-8")
first_line = meminfo.splitlines()[0]
print("first field of /proc/meminfo:", first_line.split()[0])

ptrace = proc / "sys" / "kernel" / "yama" / "ptrace_scope"
print("ptrace_scope file exists:", ptrace.exists())
print("ptrace_scope value is a digit:", ptrace.read_text(encoding="utf-8").strip().isdigit())

cgroup_root = Path("/sys/fs/cgroup")
print("is a directory:", cgroup_root.is_dir())
print("has a cgroup.controllers file:", (cgroup_root / "cgroup.controllers").exists())

# Iterating a directory: the numbered entries in /proc are processes (pids).
pids = [p.name for p in proc.iterdir() if p.name.isdigit()]
print("found some processes:", len(pids) > 0)
