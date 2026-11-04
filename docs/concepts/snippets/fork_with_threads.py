# requires: linux
# Gate G1.8: does fork() with a second thread running emit a DeprecationWarning?
import os
import re
import threading
import warnings

stop = threading.Event()
thread = threading.Thread(target=stop.wait)
thread.start()  # now this process has 2 threads

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    pid = os.fork()
    if pid == 0:
        os._exit(0)  # child: leave at once
os.waitpid(pid, 0)
stop.set()
thread.join()

print("threads alive at fork time: 2")
for warning in caught:
    message = re.sub(r"pid=\d+", "pid=N", str(warning.message))
    print(f"{warning.category.__name__}: {message}")
if not caught:
    print("no warning was emitted")
