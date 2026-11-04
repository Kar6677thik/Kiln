# Sockets and Socketpairs: How Kiln's Processes Talk

> **Milestone:** K1 · **Read before:** [files-and-file-descriptors](files-and-file-descriptors.md), [what-is-a-server](what-is-a-server.md) · **Reading time:** ~15 min
> **Terms introduced:** **socket**, **Unix domain socket**, **socketpair**, **listen**, **accept**, **backlog**, **connection refused**

## One-sentence summary

A **socket** is a file descriptor you can both read from and write to, connected to another socket somewhere else. On the same machine it can be a **TCP** connection through `127.0.0.1`, or a **Unix domain socket** that never touches the network code at all.

## Why you should care

Every conversation in Kiln is a socket ([spec/01 §3](../spec/01-architecture.md#3-connections-channels)):
- clients talk to the master over TCP on `127.0.0.1:7340`;
- the master talks to each worker over its own **socketpair**;
- even signals reach the master's loop through a socketpair (the wakeup fd, see [signals](signals.md)).

Knowing what each call does, and what "connection refused" really means, explains half of K1's behaviour.

## The mental model

**Analogy:** a telephone line. A socket is a handset: you can talk *and* listen on it. A TCP server is a switchboard with a public number (the **port**). It **listens** for calls, and when it **accepts** one, it hands you a *new* handset for that one conversation, while the switchboard keeps waiting for more calls. A **socketpair** is two handsets wired directly together, with no switchboard and no number: whoever holds the two ends can talk immediately.

**Where the analogy fails:** a phone call carries sentences. A stream socket carries a *stream of bytes* with no sentence boundaries ([framing](framing.md)). And "picking up" happens in the kernel: a call can be connected before your program calls `accept()` (see backlog).

```
  TCP (clients ↔ master)                          socketpair (master ↔ worker)
  client                master
  ┌────────┐  connect   ┌──────────────┐            master          worker
  │ socket │──────────▶ │ listening    │           ┌───────┐       ┌───────┐
  └────────┘            │ socket :7340 │           │ end A │◀─────▶│ end B │
      ▲                 └──────┬───────┘           └───────┘       └───────┘
      │     accept() returns   │                   created already connected,
      └──────── a NEW socket ◀─┘                   before fork(); each process keeps one end
```

## See it for yourself

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/sockets-and-socketpairs_1.py
```

```text
right received: b'hello from left'
left received:  b'hi back from right'
family: AF_UNIX, type: SOCK_STREAM
server got b'ping' from 127.0.0.1
listener and connection are different sockets: True
connecting to a closed port: ConnectionRefusedError (Connection refused)
```

1. **socketpair:** data flows both ways, so it's *bidirectional*, unlike a pipe. Its family is `AF_UNIX` (a **Unix domain socket**, local only) and its type is `SOCK_STREAM` (a byte stream). Note the data is `bytes` (`b'...'`), never `str`; see [bytes-vs-str](../python/bytes-vs-str.md).
2. **TCP:** the server `bind`s to `127.0.0.1` port 0 (the kernel picks a free port), `listen`s, the client connects, and `accept()` returns a **new** socket for that client. The listening socket stays separate and keeps accepting.
3. **Connection refused:** after the listener was closed, connecting to its port failed immediately with `ConnectionRefusedError`. On localhost, that means "nobody is listening on that port right now".

## How it really works

- `socket.socket(AF_INET, SOCK_STREAM)` → the `socket(2)` system call returns a new fd. `AF_INET` means IPv4 and `SOCK_STREAM` means TCP: reliable, in order, a byte stream.
- **Server side:**
  - `bind((host, port))` claims the address.
  - **`listen(backlog)`** turns the socket into a listening socket. The **backlog** is how many fully connected clients the kernel will queue while your program hasn't called `accept()` yet; clients beyond that wait or fail.
  - **`accept()`** takes one queued connection and returns a new socket plus the client's address.
- **Client side:** `connect((host, port))`. If no socket is listening there, the kernel answers right away with a reset, and Python raises `ConnectionRefusedError` (**connection refused**). Kiln relies on this on purpose: the master opens its listener only after the first worker is ready ([ADR-013](../decisions.md#adr-013)), and `kiln submit` retries while it gets "refused".
- **`socket.socketpair()`** calls `socketpair(2)`: two connected `AF_UNIX` stream sockets in one step, with no addresses and no ports. Created before `fork()`, one end stays in the master and the child keeps the other. Nobody else on the machine can connect to it, which is exactly what a private master↔worker channel needs.
- **`send`/`recv` semantics:**
  - `send` may accept only *part* of your data when the kernel's buffer is full (`sendall` loops until done, but it blocks).
  - `recv(n)` returns *up to* n bytes, whatever has arrived. It returns `b""` when the other side has closed.
  - In the master all sockets are non-blocking, so both calls can also raise `BlockingIOError`, meaning "nothing right now" (K1 primer `non-blocking-io-and-selectors`).
- Kiln binds only to `127.0.0.1` (localhost), so nothing outside your computer can connect ([ADR-021](../decisions.md#adr-021)).

## How Kiln uses it

- [`launch/fork.py` → `fork_worker`](../../src/kiln/launch/fork.py) creates one `socketpair()` per worker, right before forking.
- [`master/connections.py`](../../src/kiln/master/connections.py) wraps every socket (clients and workers alike) with input and output buffers.
- [`master/arbiter.py`](../../src/kiln/master/arbiter.py) creates the TCP listener on `master.bind` after the first `ready`, and accepts up to 64 clients per loop pass.

## Common misconceptions

- ❌ **Myth:** "One `send` on one side equals one `recv` on the other."
  ✅ **Reality:** a stream socket has no message boundaries. Sends can be merged or split. See [framing](framing.md).
- ❌ **Myth:** "The listening socket is the connection to the client."
  ✅ **Reality:** `accept()` returns a *new* socket per client. The listening socket only produces connections.
- ❌ **Myth:** "Connection refused means a firewall blocked me."
  ✅ **Reality:** on localhost it almost always means no program is listening on that port at this moment, for example because the server hasn't started yet.

## Check yourself

1. Why does Kiln use a socketpair for master↔worker instead of having workers connect to the master's TCP port?
   <details><summary>Answer</summary>

   A socketpair is created already connected, before `fork()`. There's no port to find, no race at startup, and no other process can connect to it. Each worker gets its own private channel, so the master knows exactly which worker sent each message (ADR-007).

   </details>
2. Predict: a client connects while the master's listener exists but the master hasn't called `accept()` yet. Does `connect()` fail?
   <details><summary>Answer</summary>

   No. The kernel completes the connection and holds it in the backlog queue until the program calls `accept()`. `connect()` only fails if nobody is listening, or the backlog is full.

   </details>
3. What does `recv` returning `b""` mean?
   <details><summary>Answer</summary>

   The other side closed its end of the connection (end of file). It doesn't mean "no data yet". For a worker, EOF on its channel means the master is gone.

   </details>

## Go deeper

- `man 7 socket`, `man 7 unix`, `man 2 socketpair`, `man 2 listen` (the backlog).
- [Python docs: `socket`](https://docs.python.org/3/library/socket.html) and the [Socket Programming HOWTO](https://docs.python.org/3/howto/sockets.html).
