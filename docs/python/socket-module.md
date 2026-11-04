# The `socket` module

> **First used in:** see the [ledger](README.md) (milestone K1) · **Needs:** [bytes-vs-str](bytes-vs-str.md), [exceptions](exceptions.md), [context-managers](context-managers.md) · **Reading time:** ~10 min
> **Terms introduced:** **socket object**, **socketpair**, **send / recv**, **sendall**, **EOF (end of file)**, **SO_REUSEADDR**, **BlockingIOError**, **ConnectionRefusedError**

## What it looks like

```python
import socket

a, b = socket.socketpair()      # two sockets, already connected to each other
a.send(b"hello")                # write bytes into one end...
data = b.recv(1024)             # ...and read them from the other: b"hello"
```

## What it means

A **socket object** is Python's handle on an operating-system socket: one end of a two-way channel that carries **bytes**, never `str` (see [bytes-vs-str](bytes-vs-str.md)). Underneath, it's a file descriptor (see [files-and-file-descriptors](../concepts/files-and-file-descriptors.md)), and `sock.fileno()` shows its number.

| Call | What it does |
|---|---|
| `socket.socketpair()` | creates **two** sockets connected to each other, inside one machine (Unix domain, `AF_UNIX`). Kiln gives each worker one pair: the master keeps one end, the worker the other |
| `socket.socket(socket.AF_INET, socket.SOCK_STREAM)` | creates a TCP socket (IPv4, byte stream) |
| `sock.bind(("127.0.0.1", 7340))` | attaches it to an address and port. Port `0` means "kernel, pick a free one" |
| `sock.listen(16)` | turns it into a **listening socket**; up to 16 not-yet-accepted connections wait in the queue (the *backlog*) |
| `conn, address = sock.accept()` | takes one waiting connection and returns a **new** socket for it |
| `socket.create_connection((host, port), timeout=2)` | the client side: connect, with a timeout |
| `sock.send(data)` | tries to write; **returns how many bytes it actually wrote**, which may be fewer than `len(data)` |
| `sock.sendall(data)` | keeps calling `send` until everything is written (blocking sockets only) |
| `sock.recv(n)` | returns **up to** `n` bytes: possibly fewer, possibly several messages glued together |
| `sock.recv(n)` returning `b""` | **EOF (end of file)**: the other side closed the connection |
| `sock.setblocking(False)` | makes calls return immediately instead of waiting (next section) |
| `sock.close()` | releases the file descriptor. `with socket.socket(...) as s:` closes it for you |

### Blocking vs non-blocking

By default a socket is *blocking*. `recv` waits until at least one byte arrives, and `accept` waits until someone connects. That's fine for a simple client, and it's how [`kiln.client`](../../src/kiln/client.py) works.

The master must never wait (ADR-006), so all its sockets are *non-blocking*. When nothing is there yet, `recv` raises **BlockingIOError**, Python's name for the operating system's `EAGAIN` ("try again later"). The master then asks a selector which sockets have data (see [selectors-module](selectors-module.md)) and only reads those.

## Why people use it

Every network program (web servers, databases, Kiln) is built on these few calls. Higher-level libraries such as HTTP clients and asyncio's streams all call them underneath. Kiln uses the `socket` module directly because seeing the raw behaviour is the lesson. Two surprises follow from it: `recv` can return half a message or two messages at once, and `send` can write only part of your data.

## How Kiln uses it

- [`src/kiln/master/arbiter.py`](../../src/kiln/master/arbiter.py) creates the TCP listener on `127.0.0.1:7340` with `SO_REUSEADDR`, and accepts client connections without blocking.
- [`src/kiln/master/connections.py`](../../src/kiln/master/connections.py) wraps each non-blocking socket. It keeps an input buffer for partial frames and an output buffer for partial `send`s.
- [`src/kiln/launch/fork.py`](../../src/kiln/launch/fork.py) creates one `socketpair()` per worker.
- [`src/kiln/client.py`](../../src/kiln/client.py) is the blocking client the `kiln` CLI uses. It retries `connect` while the master isn't listening yet ("connection refused").

## Gotchas

- **`recv(1024)` is not "one message".** The REPL below sends `b"ab"` and then `b"cd"`, and a single `recv` returns `b'abcd'`. TCP and stream sockets carry a stream of bytes with no boundaries; that's why Kiln needs [framing](../spec/02-protocol.md#1-why-framing-is-needed-read-this-if-tcp-is-new).
- **`send` may write less than you gave it.** Always check the return value, or use `sendall` on blocking sockets. The master keeps the unsent rest in an output buffer.
- **`b""` means closed, not "no data".** On a non-blocking socket, "no data yet" is `BlockingIOError`; `b""` always means the peer has gone.
- **"Address already in use" after a restart.** A just-closed TCP port lingers in the kernel for a while (the `TIME_WAIT` state). `setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)` before `bind` lets a restarted server reuse it immediately. **SO_REUSEADDR** is the name of that option.
- **ConnectionRefusedError** means nothing is listening on that port, as in the second half of the snippet. For Kiln it usually means the master hasn't opened its listener yet (ADR-013), or isn't running.

## Try it

```text
>>> import socket
>>> a, b = socket.socketpair()
>>> a.send(b"hello")
5
>>> b.recv(1024)
b'hello'
>>> a.send(b"ab")
2
>>> a.send(b"cd")
2
>>> b.recv(1024)
b'abcd'
>>> b.setblocking(False)
>>> try:
...     b.recv(1024)
... except BlockingIOError as error:
...     print("no data yet:", error)
... 
no data yet: [Errno 11] Resource temporarily unavailable
>>> a.close()
>>> b.recv(1024)
b''
>>> b.close()
```

(Generated from [`snippets/socket-module.repl`](snippets/socket-module.repl) with `scripts/repl_transcript.py` on Python 3.15.1.) `send` returned the number of bytes written. Two sends arrived as one `b'abcd'`. Errno 11 is `EAGAIN`. After `a.close()`, `recv` returned `b''`.

A complete TCP conversation (listen, connect, accept, send, close, then "refused") is in [`snippets/socket-module_1.py`](snippets/socket-module_1.py):

```bash
(wsl) $ ./scripts/dev.sh run docs/python/snippets/socket-module_1.py
```

```text
listening on 127.0.0.1 port chosen by the kernel: True
accepted a connection from 127.0.0.1
server received: b'submit labs.echo'
client received: b'ok'
after the client closes, recv returns: b''
connect again: ConnectionRefusedError
```

## Check yourself

1. A client sends two 10-byte messages quickly. The server calls `recv(4096)` once. How many bytes might it get?
   <details><summary>Answer</summary>

   Anything from 1 to 20. The bytes may arrive together (20), the first message may be split (fewer than 10), or anything in between. Sockets carry a byte stream, not messages, so the receiver must find the message boundaries itself (framing).

   </details>
2. On a non-blocking socket, what's the difference between `recv` raising `BlockingIOError` and `recv` returning `b""`?
   <details><summary>Answer</summary>

   `BlockingIOError` means "no data right now, try again later"; the connection is still open. `b""` means end of file: the other side closed the connection, and no more data will ever come.

   </details>
3. `sent = sock.send(data)` returned `3`, but `len(data)` is `10`. What must the program do?
   <details><summary>Answer</summary>

   Keep the remaining `data[3:]` and send it later, when the socket is writable again. Kiln's master puts it in the connection's output buffer and asks the selector for `EVENT_WRITE`.

   </details>
