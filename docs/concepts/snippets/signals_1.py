# requires: linux
# Signals: catch one with a handler, ignore one, and see a default action kill a child.
import os
import signal
import time

received = []


def on_usr1(signum, frame):
    # Runs in the main thread, between two bytecodes of whatever it was doing.
    received.append(signal.Signals(signum).name)


signal.signal(signal.SIGUSR1, on_usr1)
os.kill(os.getpid(), signal.SIGUSR1)  # send a signal to ourselves
time.sleep(0.01)  # give Python a moment to run the handler
print(f"handler saw: {received}")

signal.signal(signal.SIGUSR2, signal.SIG_IGN)  # ignore SIGUSR2 from now on
os.kill(os.getpid(), signal.SIGUSR2)
print("SIGUSR2 was ignored; we are still running")

pid = os.fork()
if pid == 0:
    signal.signal(signal.SIGTERM, signal.SIG_DFL)  # default action for SIGTERM: terminate
    time.sleep(5)  # will be interrupted
    os._exit(0)
os.kill(pid, signal.SIGTERM)
_, status = os.waitpid(pid, 0)
print(f"child killed by: {signal.Signals(os.WTERMSIG(status)).name}")
