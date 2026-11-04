# requires: linux
# A tiny fork demo: the child reports in and exits with code 3; the parent reaps it.
import os
import sys

print(f"parent: my pid is {os.getpid()}")
# Flush before forking, or the child inherits unprinted text and prints it twice.
sys.stdout.flush()

pid = os.fork()
if pid == 0:
    # This branch runs only in the CHILD, where fork() returned 0.
    print(f"child: my pid is {os.getpid()}, my parent is {os.getppid()}")
    sys.stdout.flush()
    os._exit(3)  # leave immediately; never fall through into the parent's code

# This part runs only in the PARENT, where fork() returned the child's pid.
done_pid, status = os.waitpid(pid, 0)  # wait for the child and collect its status
print(f"parent: child {done_pid} finished")
print(f"parent: exited normally? {os.WIFEXITED(status)}, exit code {os.WEXITSTATUS(status)}")
