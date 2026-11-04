# requires: linux
# Why a forked child must end with os._exit(): sys.exit() runs the PARENT's atexit handlers too.
import atexit
import os
import sys

role = "parent"


def goodbye():
    print(f"  atexit handler ran in: {role}")
    sys.stdout.flush()


atexit.register(goodbye)  # registered in the parent, BEFORE forking
sys.stdout.flush()  # empty the buffer first, so nothing is printed twice

for how in ("sys.exit", "os._exit"):
    pid = os.fork()
    if pid == 0:
        role = f"child that called {how}()"
        if how == "sys.exit":
            sys.exit(0)  # normal shutdown: runs the inherited atexit handler!
        os._exit(0)  # immediate exit: no atexit, no buffer flushing
    os.waitpid(pid, 0)
    print(f"child using {how}() has exited")
    sys.stdout.flush()

print("parent is about to exit normally")
