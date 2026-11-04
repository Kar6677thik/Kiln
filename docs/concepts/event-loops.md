# Event Loops

> **Milestone:** K1 (asyncio part added in K5) · **Read before:** [non-blocking-io-and-selectors](non-blocking-io-and-selectors.md) · **Reading time:** ~12 min
> **Terms introduced:** **event loop**, **event**, **timer**, **deadline**, **min-heap**, **callback**

## One-sentence summary

An **event loop** is a `while True` loop that sleeps until *something happens* (a socket becomes ready, a timer expires) and then runs a small piece of code for each **event**, never waiting inside that code.

## Why you should care

The Kiln master is one event loop ([spec/01 §4](../spec/01-architecture.md#4-the-masters-event-loop-the-heart-of-kiln)). Every behaviour in K1 is "an event, handled in the loop":
- a client sends `submit`;
- a worker reports a result;
- a job's timeout **deadline** passes;
- a worker misses its heartbeat;
- `SIGHUP` arrives.

If you understand the loop's shape, you can follow any master feature from cause to effect. asyncio (K4, K5) is the same idea packaged up: research [§2.6](../research/python-mastery-research.md#26-asyncio-blocking-calls-on-the-event-loop)'s "blocked event loop" incident is what happens when one handler breaks the rule "never wait".

## The mental model

**Analogy:** an air-traffic controller. They don't fly planes. They watch the radar (the selector) and a list of scheduled events sorted by time (the timers), and each time something needs attention they issue one short instruction and go back to watching.

**Where the analogy fails:** a controller can talk to two pilots at once. The loop truly does **one thing at a time**. While it handles one event, nothing else is noticed. That's why every handler must be quick.

```
 ┌──────────────────────────────── loop ─────────────────────────────────┐
 │ 1. timeout = (earliest timer deadline) − now   (or "forever" if none) │
 │ 2. ready   = selector.select(timeout)          ← the ONLY place we wait│
 │ 3. for each ready socket: handle it (read/parse/answer)               │
 │ 4. for each timer whose deadline has passed: fire it                  │
 │ 5. back to 1                                                          │
 └───────────────────────────────────────────────────────────────────────┘
   timers live in a MIN-HEAP: heap[0] is always the soonest deadline
```

## See it for yourself

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/event-loops_1.py
```

```text
t=0.10s  timer fired: say hello
t=0.30s  timer fired: send a message
t=0.30s  socket readable: b'ping'
t=0.50s  timer fired: stop
```

The snippet (about 40 lines) is a complete miniature of the master's loop:
- Three **timers** are pushed onto a heap *out of order* (0.30, 0.10, 0.50 s), and they fire **in deadline order** anyway. That's the heap at work.
- At 0.30 s a timer writes `b'ping'` into a socket. On the *next* pass the selector reports the other end readable. A timer handler caused a socket event, and the loop handled both without any threads.
- Between events the program is **asleep inside `select()`**, using no CPU. The `timeout` passed to `select` is "time until the nearest timer", so it wakes up exactly when needed.

## How it really works

- **Timers as a min-heap.** Python's `heapq` keeps a list arranged so `heap[0]` is always the smallest item. `heappush` and `heappop` cost O(log n). Storing `(deadline, id, ...)` tuples means the soonest deadline is always at the front. Kiln's `TimerHeap` adds **lazy deletion**: cancelling a timer only marks its id as dead, and dead timers are skipped when they reach the front. That's much cheaper than searching the heap.
- **Monotonic time.** Deadlines use `time.monotonic()`, which never jumps backwards, unlike the wall clock when the system clock gets corrected.
- **Callbacks and the "never block" rule.** In a loop, a handler is often called a **callback**: code the loop calls when its event happens. A callback that sleeps, waits for a lock, or reads a blocking socket stops *the whole loop*, because there's no other thread to notice anything else. In the master, that would mean no dispatching, no heartbeat checks, no signal handling.
- **Signals become events too.** A signal can interrupt the program at any moment. Kiln turns it into an ordinary readable socket (the wakeup fd, see [signals](signals.md)), so signals are handled at step 3 like everything else.
- **asyncio is this loop, packaged.** `asyncio` runs the same select-then-dispatch loop. `async def` functions are written so they *pause* at each `await` instead of blocking, and the loop resumes them when their socket or timer is ready. K5 builds on exactly this.

## How Kiln uses it

- [`master/arbiter.py` → `Arbiter.run`](../../src/kiln/master/arbiter.py): the loop itself, following the five steps above.
- [`master/timers.py` → `TimerHeap`](../../src/kiln/master/timers.py): job timeouts, wait timeouts, boot and retire deadlines, heartbeat checks, respawn backoff, result eviction.
- Every handler in the master returns quickly. The worst case is one `recv`, one frame decode, one `send` into a buffer.

## Common misconceptions

- ❌ **Myth:** "an event loop runs things in parallel."
  ✅ **Reality:** it runs one thing at a time, very quickly, and only *waits* in one place. The illusion of parallelism comes from never waiting anywhere else.
- ❌ **Myth:** "`time.sleep(0.1)` inside a handler is harmless, it's short."
  ✅ **Reality:** for 0.1 s nothing else in the process happens. At 1,000 events per second that's 100 delayed events per sleep.
- ❌ **Myth:** "timers fire exactly on time."
  ✅ **Reality:** they fire *at the first loop pass after* their deadline. A slow handler delays every timer behind it. K5 measures this delay as "loop lag".

## Check yourself

1. Why is `select()` the only call in the loop allowed to wait?
   <details><summary>Answer</summary>

   It's the one place where waiting means "waiting for *any* event", sockets and the next timer included. Waiting anywhere else means waiting for *one* thing while ignoring everything else.

   </details>
2. Three timers are scheduled for 5 s, 1 s and 3 s. What timeout does the loop pass to `select()` right away?
   <details><summary>Answer</summary>

   About 1 s: the time until the *nearest* deadline, which is `heap[0]`. After that timer fires, the next timeout becomes about 2 s.

   </details>
3. Predict: a handler accidentally runs `time.sleep(2)`, and a timer is due 0.5 s later. When does the timer fire?
   <details><summary>Answer</summary>

   After about 2 s, as soon as the handler returns and the loop reaches step 4. It's 1.5 s late, because nothing can run while the handler sleeps.

   </details>
4. Why does `TimerHeap` mark cancelled timers instead of removing them?
   <details><summary>Answer</summary>

   Removing an arbitrary item from a heap means searching for it, O(n), and re-heapifying. Marking it dead is O(1), and the dead entry is discarded cheaply when it reaches the top.

   </details>

## Go deeper

- Python docs, [`heapq`](https://docs.python.org/3/library/heapq.html): includes the "priority queue implementation notes" behind lazy deletion.
- Python docs, [asyncio event loop](https://docs.python.org/3/library/asyncio-eventloop.html): the packaged version (K5).
- gunicorn's `gunicorn/arbiter.py` (`Arbiter.run`, `sleep`, `wakeup`): a production master loop in the same style, which K1's reference comparison reads.
