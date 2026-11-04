# A mini timer heap with lazy deletion, the same idea as Kiln's TimerHeap.
import heapq
import itertools

heap = []
cancelled = set()
ids = itertools.count(1)


def schedule(deadline, kind):
    timer_id = next(ids)
    heapq.heappush(heap, (deadline, timer_id, kind))
    return timer_id


def pop_expired(now):
    fired = []
    while heap and heap[0][0] <= now:
        deadline, timer_id, kind = heapq.heappop(heap)
        if timer_id in cancelled:  # lazy deletion: skip timers cancelled earlier
            cancelled.discard(timer_id)
            continue
        fired.append((deadline, kind))
    return fired


schedule(10.0, "job_timeout")
heartbeat = schedule(2.0, "heartbeat_check")
schedule(5.0, "respawn")
cancelled.add(heartbeat)  # the worker answered in time: this timer no longer matters
print("next deadline:", heap[0][0])
print("fired at t=6:", pop_expired(6.0))
print("fired at t=11:", pop_expired(11.0))
print("left:", heap)
