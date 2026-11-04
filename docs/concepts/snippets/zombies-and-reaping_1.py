# requires: linux
# A dead child stays a "zombie" until its parent reaps it with waitpid().
import os
import time
from pathlib import Path


def state_of(pid):
    stat = Path(f"/proc/{pid}/stat")
    if not stat.exists():
        return "gone (no /proc entry)"
    # The 3rd field of /proc/<pid>/stat is the state letter: R running, S sleeping, Z zombie.
    return stat.read_text().rsplit(")", 1)[1].split()[0]


pid = os.fork()
if pid == 0:
    os._exit(7)  # the child dies immediately

time.sleep(0.2)  # give it time to die, but do NOT reap it yet
print(f"after the child exited, its state is: {state_of(pid)}")

reaped_pid, status = os.waitpid(pid, os.WNOHANG)
print(f"waitpid returned the child? {reaped_pid == pid}; exit code {os.WEXITSTATUS(status)}")
print(f"after reaping, its state is: {state_of(pid)}")

# WNOHANG with a child that is still alive returns (0, 0) instead of waiting.
sleeper = os.fork()
if sleeper == 0:
    time.sleep(0.3)
    os._exit(0)
print(f"waitpid(-1, WNOHANG) while a child is still running: {os.waitpid(-1, os.WNOHANG)}")
os.waitpid(sleeper, 0)

# With no children at all, waitpid raises ChildProcessError.
try:
    os.waitpid(-1, os.WNOHANG)
except ChildProcessError as error:
    print(f"waitpid with no children left: ChildProcessError ({error.strerror})")
