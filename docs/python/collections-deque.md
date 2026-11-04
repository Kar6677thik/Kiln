# collections.deque (a fast queue)

> **First used in:** see the [ledger](README.md) (milestone K1) · **Needs:** [variables-and-types](variables-and-types.md) · **Reading time:** ~5 min
> **Terms introduced:** **deque**, **O(1)**, **O(n)**

## What it looks like

```python
from collections import deque

queue = deque()
queue.append("job-1")     # join the back of the line
queue.append("job-2")
first = queue.popleft()   # serve the front of the line: "job-1"
```

## What it means

A **deque** (pronounced "deck", short for "double-ended queue") is a list-like container that is fast at **both ends**:

| Operation | What it does |
|---|---|
| `append(x)` / `appendleft(x)` | add at the right (back) / left (front) |
| `pop()` / `popleft()` | remove and return from the right / left |
| `len(q)`, `q[0]`, `q[-1]` | size, first item, last item |
| `q.remove(x)` | remove the first occurrence of `x` (this one is slow: it searches) |

A FIFO queue (first in, first out) is "`append` at the back, `popleft` at the front".

## Why people use it

A normal `list` can do the same with `items.pop(0)`, but that's slow: removing the first item makes Python shift **every** other item one place left. Computer scientists call that **O(n)**: the time grows with the number of items `n`. A deque removes from either end in **O(1)**: constant time, no matter how long the queue is. With 50,000 queued jobs, that difference matters on every dispatch.

## How Kiln uses it

- [`src/kiln/master/jobs.py`](../../src/kiln/master/jobs.py): `JobTable.queue` is a `deque[str]` of job ids. `submit` appends, the dispatcher takes from the front with `pop_next()`, a retried job goes to the **back** (spec/03 §6), and a job that couldn't be placed is put back at the front with `appendleft`.
- Cancelling a queued job uses `remove`, which is O(n). That's acceptable: cancels are rare compared with dispatches.

## Gotchas

- **`popleft()` on an empty deque raises `IndexError`.** Check `if queue:` first. (An empty deque is "falsy", like an empty list.)
- **Indexing the middle is slow.** `q[0]` and `q[-1]` are fast; `q[25_000]` walks towards the middle. A deque is for ends, not random access.
- **Don't change a deque while looping over it** (`for x in q: q.remove(...)`). Python raises `RuntimeError: deque mutated during iteration`.

## Try it

```text
>>> from collections import deque
>>> queue = deque()
>>> queue.append("job-1")
>>> queue.append("job-2")
>>> queue.append("job-3")
>>> queue
deque(['job-1', 'job-2', 'job-3'])
>>> queue.popleft()
'job-1'
>>> queue
deque(['job-2', 'job-3'])
>>> queue.appendleft("job-1")
>>> queue
deque(['job-1', 'job-2', 'job-3'])
>>> len(queue)
3
>>> queue[0]
'job-1'
>>> queue.remove("job-2")
>>> queue
deque(['job-1', 'job-3'])
>>> empty = deque()
>>> empty.popleft()
Traceback (most recent call last):
  File "<console>", line 1, in <module>
IndexError: pop from an empty deque
```

(Generated from [`snippets/collections-deque.repl`](snippets/collections-deque.repl) on Python 3.15.1.)

## Check yourself

1. Which two methods make a deque behave as a FIFO queue?
   <details><summary>Answer</summary>

   `append` (add to the back) and `popleft` (take from the front).

   </details>
2. Why not use a list with `pop(0)` for Kiln's queue?
   <details><summary>Answer</summary>

   `list.pop(0)` shifts every remaining item, so it gets slower as the queue grows (O(n)). `deque.popleft()` takes the same time however many jobs are queued (O(1)).

   </details>
3. The dispatcher took a job with `popleft()` but no worker had a free slot. How does it put the job back without breaking FIFO order?
   <details><summary>Answer</summary>

   `appendleft(job_id)`: it goes back to the front, where it was. `append` would wrongly send it to the back of the line.

   </details>
