# heapq (always know the smallest item)

> **First used in:** see the [ledger](README.md) (milestone K1) · **Needs:** [variables-and-types](variables-and-types.md), [collections-deque](collections-deque.md) · **Reading time:** ~7 min
> **Terms introduced:** **heap**, **min-heap**, **priority queue**, **lazy deletion**

## What it looks like

```python
import heapq

timers = []                                          # a heap is a plain list
heapq.heappush(timers, (30.0, 1, "job_timeout"))     # (deadline, id, kind)
heapq.heappush(timers, (5.0, 2, "heartbeat_check"))
soonest = timers[0]                                  # (5.0, 2, 'heartbeat_check')
heapq.heappop(timers)                                # removes and returns the soonest
```

## What it means

A **heap** is a list kept in a special order. Python's `heapq` maintains a **min-heap**: the **smallest** item is always at index 0.

| Function | Effect | Cost |
|---|---|---|
| `heapq.heappush(heap, item)` | insert, keeping the heap order | O(log n): fast even for big heaps |
| `heapq.heappop(heap)` | remove and return the smallest item | O(log n) |
| `heap[0]` | look at the smallest item without removing it | O(1) |

The rest of the list is *not* fully sorted, only "smallest first". That's what makes it cheap.

Items are compared like tuples: first by the first element, and on a tie, by the second, and so on. That's why Kiln stores **`(deadline, id, kind)`**: the earliest deadline wins, and the unique `id` breaks ties, so Python never has to compare two `kind`s or other objects that can't be compared.

A heap used like this ("always give me the most urgent item next") is called a **priority queue**.

## Why people use it

Kiln's master has many deadlines at once: job timeouts, heartbeat checks, respawn delays. Each loop pass must know "how long until the *next* deadline?", to sleep exactly that long in `select()`. Scanning every timer each time would cost O(n). With a heap it's `heap[0]`.

**Cancelling** is the awkward part: removing an item from the *middle* of a heap is slow. The standard trick is **lazy deletion**: remember the cancelled ids in a set, and skip those entries when they reach the top. A job that finishes before its timeout just marks its timer cancelled.

## How Kiln uses it

- [`src/kiln/master/timers.py`](../../src/kiln/master/timers.py): `TimerHeap.schedule(deadline_mono, kind, key)` pushes `(deadline, timer_id, ...)`, `cancel(timer_id)` adds to a cancelled set, `pop_expired(now)` pops everything due and skips cancelled ids, and `next_deadline()` reads `heap[0]` ([K1 spec §5.6](../milestones/K1-supervisor.md#56-kilnmaster-the-other-modules)).
- [`src/kiln/master/jobs.py`](../../src/kiln/master/jobs.py): result expiry is a heap of `(expires_mono, job_id)`, so the oldest results are evicted first (spec/03 §3).

## Gotchas

- **Only `heapq` functions keep the order.** `timers.append(x)` or `timers.sort(reverse=True)` breaks the heap silently; the next `heappop` returns the wrong item.
- **Ties need a tie-breaker.** Without a unique second element, two timers with the same deadline would compare their third elements. Comparing two dicts raises `TypeError`.
- **Lazy deletion leaves garbage in the heap** until it reaches the top. For Kiln's sizes that's fine; very long-lived servers sometimes rebuild the heap occasionally.

## Try it

```text
>>> import heapq
>>> timers = []
>>> heapq.heappush(timers, (30.0, 1, "job_timeout"))
>>> heapq.heappush(timers, (5.0, 2, "heartbeat_check"))
>>> heapq.heappush(timers, (12.5, 3, "respawn"))
>>> timers[0]
(5.0, 2, 'heartbeat_check')
>>> heapq.heappop(timers)
(5.0, 2, 'heartbeat_check')
>>> heapq.heappop(timers)
(12.5, 3, 'respawn')
>>> timers
[(30.0, 1, 'job_timeout')]
>>> cancelled = {3}
>>> deadline, timer_id, kind = heapq.heappop(timers)
>>> timer_id in cancelled
False
>>> heapq.heappush(timers, (7.0, 4, "a"))
>>> heapq.heappush(timers, (7.0, 5, "b"))
>>> heapq.heappop(timers)
(7.0, 4, 'a')
```

(Generated from [`snippets/heapq.repl`](snippets/heapq.repl) on Python 3.15.1.) The last three lines show the tie-break: both deadlines are 7.0, so the smaller id (4) wins.

A complete mini timer heap with lazy deletion is in [`snippets/heapq_1.py`](snippets/heapq_1.py):

```bash
(wsl) $ ./scripts/dev.sh run docs/python/snippets/heapq_1.py
```

```text
next deadline: 2.0
fired at t=6: [(5.0, 'respawn')]
fired at t=11: [(10.0, 'job_timeout')]
left: []
```

The heartbeat timer at t=2 was cancelled, so it never "fires", even though it was the soonest.

## Check yourself

1. You push `(9, 1)`, `(3, 2)` and `(6, 3)`. What does `heap[0]` show?
   <details><summary>Answer</summary>

   `(3, 2)`: the smallest first element is always at index 0.

   </details>
2. Why does each timer tuple contain a unique id as its second element?
   <details><summary>Answer</summary>

   To break ties between equal deadlines. Without it, Python would compare the next elements, which may be values that can't be compared (raising `TypeError`), or would order equal deadlines unpredictably.

   </details>
3. A job finishes before its timeout. Why doesn't Kiln remove the timeout from the heap right away?
   <details><summary>Answer</summary>

   Removing from the middle of a heap is slow. Marking the timer id as cancelled is instant; the entry is discarded when it reaches the top (lazy deletion).

   </details>
