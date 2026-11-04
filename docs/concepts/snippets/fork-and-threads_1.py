# requires: linux
# A lock held by ANOTHER thread at fork time stays locked forever in the child.
import os
import sys
import threading
import warnings

lock = threading.Lock()
holding = threading.Event()
release = threading.Event()


def holder():
    with lock:  # this thread takes the lock...
        holding.set()
        release.wait()  # ...and keeps it until told to let go


thread = threading.Thread(target=holder)
thread.start()
holding.wait()  # make sure the lock is taken before we fork

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)  # we know; that's the point
    pid = os.fork()

if pid == 0:
    # The child got a copy of the LOCKED lock, but NOT the thread that would release it.
    got_it = lock.acquire(timeout=0.5)
    print(f"child:  threads in me = {threading.active_count()}, lock acquired? {got_it}")
    sys.stdout.flush()  # os._exit() does not flush buffers, so flush by hand
    os._exit(0)

os.waitpid(pid, 0)
release.set()  # in the parent, the holder thread really exists and lets go
thread.join()
print(f"parent: lock acquired after the holder released it? {lock.acquire(timeout=0.5)}")
