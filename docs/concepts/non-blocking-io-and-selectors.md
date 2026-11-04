# Non-Blocking I/O and Selectors

> **Milestone:** K1 · **Read before:** [files-and-file-descriptors](files-and-file-descriptors.md), [what-is-a-server](what-is-a-server.md) · **Reading time:** ~15 min
> **Terms introduced:** **blocking call**, **non-blocking I/O**, **EAGAIN**, **BlockingIOError**, **selector**, **epoll**, **readiness**, **output buffer**

## One-sentence summary

A **non-blocking** socket never makes your program wait: it either does what it can right now or says "not now". A **selector** is the tool that tells you *when* "now" has arrived for any of many sockets at once.

## Why you should care

Kiln's master has **exactly one thread** ([ADR-006](../decisions.md#adr-006)), and that one thread has to look after everything at once: the listening socket, dozens of client connections, one channel per worker, and the signal pipe. If it ever called a function that *waits*, such as reading a socket that has no data yet, every other connection would freeze until that one wait ended. One slow client would stall every job in the system.

The same mistake appears later in asyncio, where it's called "blocking the event loop" (research [§2.6](../research/python-mastery-research.md#26-asyncio-blocking-calls-on-the-event-loop), K5). The tools here (non-blocking sockets plus a selector) are what asyncio is built on underneath.

## The mental model

**Analogy:** a restaurant with one waiter and many tables.
- A **blocking** waiter walks to table 3, asks "ready to order?", and *stands there* until they decide. Tables 1, 2 and 4 wait for nothing.
- A **non-blocking** waiter asks, hears "not yet", and moves on immediately.
- A **selector** is a buzzer on every table. The waiter stands in the middle and is told "tables 2 and 4 are ready", then serves only those.

**Where the analogy fails:** a buzzer means "ready to order", but socket **readiness** only means "a read won't wait *right now*". It doesn't say how much data is there; it might be half a message ([framing](framing.md)). And writing can also stop being possible, when the kernel's outgoing buffer is full.

```
                         ┌─────────────── one thread ───────────────┐
  listener   ──┐         │                                          │
  client A   ──┤         │   events = selector.select(timeout)      │
  client B   ──┼──epoll──▶   for each READY fd: recv()/send() once  │
  worker 0   ──┤         │   (never "until done"; never waits)      │
  signal fd  ──┘         │                                          │
                         └──────────────────────────────────────────┘
```

## See it for yourself

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/non-blocking-io-and-selectors_1.py
```

```text
DefaultSelector is EpollSelector
recv with no data -> BlockingIOError (errno 11, EAGAIN)
select(timeout=0.1) with nothing sent -> []
ready: pair B -> b'hello from B'
send buffer full after 227 KiB: send() raised BlockingIOError
```

Line by line:
1. On Linux, Python's `selectors.DefaultSelector` is the **epoll** selector, the most efficient one the kernel offers (verification gate G1.1 ✅).
2. A non-blocking `recv` with nothing to read doesn't wait. It raises **`BlockingIOError`**, Python's name for the kernel error **EAGAIN** (number 11: "try again later").
3. With nothing sent, `select(timeout=0.1)` sleeps up to 0.1 s and returns an empty list: no socket is ready.
4. After sending on pair B, the selector reports *only* pair B as ready, together with the `data` label we attached when registering it.
5. Sending can fail too. The kernel accepted about 227 KiB into its buffer for the peer, then refused more. That's why every Kiln connection keeps its own **output buffer** and finishes writing later.

## How it really works

- `sock.setblocking(False)` sets the `O_NONBLOCK` flag on the file descriptor. From then on, `recv` and `send` return immediately. The underlying syscalls `recvfrom(2)` and `sendto(2)` fail with `EAGAIN` instead of sleeping.
- **epoll** (`man 7 epoll`) is a kernel object holding an "interest list" of fds. `epoll_wait(2)` sleeps until at least one fd in the list becomes readable or writable, or the timeout expires. Unlike the older `select(2)`, its cost doesn't grow with the number of idle fds.
- Python's `selectors` module wraps this:
  - `register(fileobj, events, data)` adds an fd with `EVENT_READ` and/or `EVENT_WRITE`;
  - `modify()` changes which events you care about;
  - `select(timeout)` returns `(key, events)` pairs. `key.data` is whatever you attached, and Kiln stores the `Connection` object there.
- **Readiness, not completion.** "Readable" means one `recv` will return *something*: maybe 1 byte, maybe 64 KiB, or `b""`, which means the other side closed. "Writable" means *some* bytes can be sent. Code must handle partial results every time.
- **Register for writing only when you have something to write.** A socket is almost always writable, so a selector watching `EVENT_WRITE` on an idle socket would wake up constantly ("busy looping"). Kiln adds `EVENT_WRITE` when a connection's output buffer is non-empty and removes it when the buffer drains.

## How Kiln uses it

- [`master/connections.py` → `Connection`](../../src/kiln/master/connections.py) holds one non-blocking socket plus an input `FrameDecoder` and an output `bytearray`. `read()` does one `recv`. `send()` only appends to the buffer. `flush()` sends what the kernel will take and keeps the rest.
- [`master/arbiter.py` → `Arbiter.run`](../../src/kiln/master/arbiter.py) is the loop that calls `selector.select(timeout)` ([spec/01 §4](../spec/01-architecture.md#4-the-masters-event-loop-the-heart-of-kiln)).
- Comment tags `CONCEPT(non-blocking-io-and-selectors)` in those files point back here.

## Common misconceptions

- ❌ **Myth:** "non-blocking means faster."
  ✅ **Reality:** each call does the same work. Non-blocking means *one thread can juggle many connections* without any of them stalling the others.
- ❌ **Myth:** "if the selector says readable, I can read a whole message."
  ✅ **Reality:** you can read *some bytes*. A message may arrive split across many reads, which is why Kiln frames messages ([framing](framing.md)).
- ❌ **Myth:** "`send()` sends everything I give it."
  ✅ **Reality:** it returns how many bytes it accepted, possibly fewer, and a non-blocking socket can refuse completely with `BlockingIOError`. (`sendall()` hides this by *blocking*, which the master must never do.)

## Check yourself

1. A non-blocking `recv` raises `BlockingIOError`. Is that a bug?
   <details><summary>Answer</summary>

   No. It's the normal "no data yet" answer (EAGAIN). The right response is to go back to `select()` and wait for readiness.

   </details>
2. `recv` returns `b""`. What happened?
   <details><summary>Answer</summary>

   The other side closed the connection (end of file). The connection should be cleaned up. For a worker channel, that's a sign the worker is gone.

   </details>
3. Why doesn't the master register every socket for `EVENT_WRITE` all the time?
   <details><summary>Answer</summary>

   Sockets are almost always writable, so `select()` would return immediately on every pass. The loop would spin and burn 100% CPU doing nothing. Writability is only interesting when there are bytes waiting in the output buffer.

   </details>
4. Predict: you register three sockets, data arrives on two of them, and you call `select(timeout=5)`. How long does it wait, and how many keys come back?
   <details><summary>Answer</summary>

   It returns almost immediately (data is already waiting) with **two** keys, one per ready socket. The timeout is only an upper limit.

   </details>

## Go deeper

- `man 7 epoll` and `man 2 epoll_wait`: the kernel interface underneath `EpollSelector`.
- Python docs, [`selectors`](https://docs.python.org/3/library/selectors.html): the module's full API, with an echo-server example.
- Python docs, [`socket.setblocking`](https://docs.python.org/3/library/socket.html#socket.socket.setblocking): blocking, timeout and non-blocking modes.
- [The C10K problem](http://www.kegel.com/c10k.html): the classic essay on why servers moved to this model.
