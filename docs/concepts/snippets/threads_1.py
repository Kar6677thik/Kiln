"""Threads share one process and its memory; processes don't.

Two threads append to the same list; a forked child appends to its own copy.
"""

import os
import threading

shared: list[str] = []


def work(tag: str) -> None:
    """Append a line saying which thread and process ran this."""
    me = threading.current_thread()
    same = "same" if os.getpid() == MAIN_PID else "other"
    shared.append(f"{tag}: thread name={me.name!r}, {same} pid as main")


MAIN_PID = os.getpid()
threads = [threading.Thread(target=work, args=(f"t{i}",), name=f"worker-{i}") for i in (1, 2)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
pid = os.fork()
if pid == 0:
    work("forked child")  # appends to the CHILD's copy of the list
    os._exit(0)
os.waitpid(pid, 0)
print(f"main thread is {threading.current_thread().name!r}")
for line in shared:
    print(line)
print(f"the parent's list has {len(shared)} entries: the child's append went to its own copy")
