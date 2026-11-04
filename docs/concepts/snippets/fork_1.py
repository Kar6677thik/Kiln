# requires: linux
# fork() makes a COPY of the running process. Both copies continue from the same line.
import os
import sys

counter = 10
print("before fork: this line sits in the output buffer, not yet written")

pid = os.fork()  # from here on, TWO processes run this code

if pid == 0:
    # Only the child sees 0.
    counter += 1  # changes the CHILD's copy of counter only
    print(f"child:  fork() returned 0, my counter = {counter}")
    sys.stdout.flush()
    os._exit(0)  # leave immediately; never run the parent's code below

# Only the parent gets here: fork() returned the child's pid (a positive number).
os.waitpid(pid, 0)  # wait for the child so the output order is predictable
print(f"parent: fork() returned a pid > 0: {pid > 0}, my counter = {counter}")
