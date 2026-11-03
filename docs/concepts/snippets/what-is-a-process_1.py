# requires: linux
# A process looks at itself, then starts a child process and reads its exit code.
import os
import subprocess
import sys

print("my pid (process id)      :", os.getpid())
print("my parent's pid          :", os.getppid())

# /proc/self is a folder the kernel makes up on the fly, describing the process reading it.
with open("/proc/self/status", encoding="utf-8") as status:
    for line in status:
        if line.startswith(("Name:", "Pid:", "PPid:", "Threads:", "VmRSS:")):
            print("  /proc/self/status ->", line.strip())

# Start a child process: a brand-new Python that prints its own ids and exits with code 3.
child_code = (
    "import os, sys; print('  child: pid', os.getpid(), 'parent', os.getppid()); sys.exit(3)"
)
# Flush our own buffered output first, so the lines appear in the order they happened.
sys.stdout.flush()
result = subprocess.run([sys.executable, "-c", child_code], check=False)
print("child finished with exit code:", result.returncode)
