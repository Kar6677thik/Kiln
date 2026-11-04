# requires: linux
# Gate G1.10: decoding the "wait status" a parent gets from waitpid().
import os
import signal
import time


def child_exit_0():
    os._exit(0)


def child_exit_3():
    os._exit(3)


def child_killed():
    os.kill(os.getpid(), signal.SIGKILL)
    time.sleep(5)


cases = [("exit 0", child_exit_0), ("exit 3", child_exit_3), ("SIGKILL", child_killed)]
for name, behaviour in cases:
    pid = os.fork()
    if pid == 0:
        behaviour()
    _, status = os.waitpid(pid, 0)
    if os.WIFEXITED(status):
        how = f"exited, code {os.WEXITSTATUS(status)}"
    else:
        how = f"killed by {signal.Signals(os.WTERMSIG(status)).name}"
    code = os.waitstatus_to_exitcode(status)
    print(f"{name:8} raw status={status:#06x}  {how:20}  waitstatus_to_exitcode={code}")
