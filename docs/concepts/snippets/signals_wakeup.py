# requires: linux
# Gate G1.2: signal.set_wakeup_fd writes the signal number into a (non-blocking) socket.
import os
import signal
import socket

read_end, write_end = socket.socketpair()
read_end.setblocking(False)
write_end.setblocking(False)
old = signal.set_wakeup_fd(write_end.fileno())

pending = []


def remember(signum, frame):
    pending.append(signum)  # a handler that does almost nothing: the Kiln rule


signal.signal(signal.SIGUSR1, remember)
signal.signal(signal.SIGUSR2, remember)

os.kill(os.getpid(), signal.SIGUSR1)
os.kill(os.getpid(), signal.SIGUSR2)
os.kill(os.getpid(), signal.SIGUSR1)

data = read_end.recv(64)  # what an event loop would read after select() woke it up
print(f"bytes written to the wakeup socket: {list(data)}")
print(f"as signal names: {[signal.Signals(b).name for b in data]}")
print(f"the Python handlers recorded: {[signal.Signals(s).name for s in pending]}")

signal.set_wakeup_fd(old)  # put things back
