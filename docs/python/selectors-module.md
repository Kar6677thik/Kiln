# The `selectors` module

> **First used in:** see the [ledger](README.md) (milestone K1) · **Needs:** [socket-module](socket-module.md) · **Reading time:** ~8 min
> **Terms introduced:** **selector**, **register / unregister**, **EVENT_READ / EVENT_WRITE**, **SelectorKey**, **readiness**

## What it looks like

```python
import selectors

sel = selectors.DefaultSelector()
sel.register(sock, selectors.EVENT_READ, data="worker-1 channel")
for key, mask in sel.select(timeout=1.0):       # wait until something is ready
    print(key.data, "is ready:", key.fileobj.recv(4096))
```

## What it means

A **selector** watches many sockets (or other file descriptors) at once, and tells you which ones are *ready*. That lets one thread serve hundreds of connections without ever blocking on any single one.

| Call | Meaning |
|---|---|
| `selectors.DefaultSelector()` | the best mechanism this OS offers. On Linux that's `EpollSelector`, built on the kernel's `epoll` |
| `sel.register(fileobj, events, data=...)` | start watching `fileobj`. `events` is `EVENT_READ`, `EVENT_WRITE`, or both combined with `\|`. `data` is any value you want handed back later, such as "which connection is this?" |
| `sel.modify(fileobj, events, data)` | change what you watch for, for example add `EVENT_WRITE` while you have unsent output |
| `sel.unregister(fileobj)` | stop watching it. Do this **before** closing the socket |
| `sel.select(timeout)` | wait up to `timeout` seconds (`0` = don't wait, `None` = wait forever). Returns a list of `(key, mask)` pairs, one per ready file |
| `key` (a **SelectorKey**) | has `key.fileobj` (your socket), `key.fd` (its number), `key.events` (what you asked for), and `key.data` (your value) |
| `mask` | which events actually happened: test it with `mask & selectors.EVENT_READ` |

**Readiness** is the key idea. `EVENT_READ` ready means "a `recv` now won't block": there's data, or the peer closed (then `recv` returns `b""`). `EVENT_WRITE` ready means "a `send` now won't block": there's room in the kernel's send buffer. The selector never reads or writes for you; it only says *when* doing so is safe.

## Why people use it

Without a selector, a single-threaded server must either block on one socket and ignore the rest, or loop over all of them asking "anything yet?" (which burns CPU). A selector asks the kernel to do the waiting: the thread sleeps until any watched socket becomes ready, or until the timeout passes. This is the heart of every event loop, including asyncio's (K5).

## How Kiln uses it

- [`src/kiln/master/arbiter.py`](../../src/kiln/master/arbiter.py) is one big `while` loop around `sel.select(timeout)`. The `timeout` is the time until the next deadline, from the timer heap. Every socket is registered with a `data` value saying what it is: the listener, a client connection, a worker channel, or the signal pipe. The loop dispatches on that value (spec/01 §4).
- [`src/kiln/master/connections.py`](../../src/kiln/master/connections.py) decides when a connection needs `EVENT_WRITE`: only while its output buffer isn't empty.

## Gotchas

- **A socket with room to send is writable almost all the time.** If you register `EVENT_WRITE` permanently, `select()` returns immediately on every pass and the loop spins at 100% CPU. Ask for `EVENT_WRITE` only while you have something unsent, then `modify` back to `EVENT_READ`. (In the REPL below, the socket was instantly reported writable.)
- **Unregister before closing.** Closing first leaves a stale entry, and the next `register` of a new socket with the same fd number fails.
- **"Ready" isn't a promise of a whole message.** It means *some* bytes, so a frame decoder is still needed (see [socket-module](socket-module.md)).
- **Readiness can disappear.** Use non-blocking sockets anyway, so a spurious wakeup gives `BlockingIOError` instead of a hang.

## Try it

```text
>>> import selectors, socket
>>> sel = selectors.DefaultSelector()
>>> sel
<selectors.EpollSelector object at 0x7ee3e9447b60>
>>> a, b = socket.socketpair()
>>> b.setblocking(False)
>>> key = sel.register(b, selectors.EVENT_READ, data="worker-1 channel")
>>> sel.select(timeout=0)
[]
>>> a.send(b"ping")
4
>>> events = sel.select(timeout=1)
>>> for key, mask in events:
...     print(key.data, mask == selectors.EVENT_READ, key.fileobj.recv(100))
... 
worker-1 channel True b'ping'
>>> key = sel.modify(b, selectors.EVENT_READ | selectors.EVENT_WRITE, data="now also writable?")
>>> for key, mask in sel.select(timeout=0):
...     print(key.data, bool(mask & selectors.EVENT_WRITE), bool(mask & selectors.EVENT_READ))
... 
now also writable? True False
>>> key = sel.unregister(b)
>>> sel.close()
>>> a.close()
>>> b.close()
```

(Generated from [`snippets/selectors-module.repl`](snippets/selectors-module.repl) on Python 3.15.1.) Line by line:

- `DefaultSelector` is `EpollSelector` on this WSL kernel, which settles K1's verification gate G1.1.
- With nothing sent, `select(timeout=0)` returns `[]`.
- After `a.send(b"ping")`, `b` is readable and `data` comes back unchanged.
- Once `EVENT_WRITE` is requested, the socket is writable immediately, but not readable, because the "ping" was already consumed.

[`snippets/selectors-module_1.py`](snippets/selectors-module_1.py) watches three sockets at once, of which two have data:

```text
watched: ['client-1', 'worker-0', 'worker-1']
readable: ['client-1', 'worker-1']
```

## Check yourself

1. The master registers every client socket for `EVENT_READ | EVENT_WRITE` and never changes it. What happens?
   <details><summary>Answer</summary>

   `select()` returns immediately on every pass, because the sockets are almost always writable. The loop never sleeps and burns a full CPU core. Ask for `EVENT_WRITE` only while there's unsent output.

   </details>
2. What is `data=` in `register` for?
   <details><summary>Answer</summary>

   It's any value you attach to the registration, returned as `key.data` when the socket is ready. Kiln stores what the socket *is* (the listener, a client connection, a worker channel), so the loop knows how to handle it without searching a table.

   </details>
3. `select()` reports a socket readable, but `recv` returns `b""`. What happened?
   <details><summary>Answer</summary>

   The peer closed the connection. EOF also counts as "readable", because `recv` won't block: it returns `b""` at once. The program should unregister and close that socket.

   </details>
