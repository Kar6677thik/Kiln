# What Is a Server?

> **Milestone:** K0 · **Read before:** [what-is-a-process](what-is-a-process.md), [files-and-file-descriptors](files-and-file-descriptors.md) · **Reading time:** ~15 min
> **Terms introduced:** **server**, **client**, **request / response**, **port**, **localhost (127.0.0.1)**, **listening socket**, **TCP**

## One-sentence summary

A **server** is a long-running process that waits for other programs (**clients**) to connect and ask it to do things. It answers each **request** with a **response**, then goes back to waiting.

## Why you should care

Kiln *is* a server. `kiln serve` starts a master process that listens on `127.0.0.1:7340`. Clients such as the `kiln submit` command connect there and send jobs ([spec/01 §1](../spec/01-architecture.md#1-the-big-picture)). From K5, a second server, the HTTP **gateway** on port 7341, joins it.

Many of the incidents this project reproduces are server problems:
- A server that stops answering because one request blocked it: the asyncio blocked-loop story in [research §2.6](../research/python-mastery-research.md#26-asyncio-blocking-calls-on-the-event-loop).
- A server that slowly eats memory because it runs for weeks: the leaks in [research §2.8](../research/python-mastery-research.md#28-memory-leaks-in-long-running-services).

These are only problems *because* a server runs "forever" and serves many clients.

## The mental model

**Analogy:** a shop with a numbered door. The building is the computer, and the door number is the **port**. The shopkeeper (the server process) unlocks the door and waits behind the counter (**listening**). Customers (clients) walk up, ask for something (a request), get it (a response), and leave or ask again. The shopkeeper never goes home; serving is the whole job.

**Where the analogy fails:** a real shopkeeper serves one customer at a time. A good server juggles hundreds of connections at once, which is what Kiln's event loop (K1) and asyncio gateway (K5) are for. And the "door" isn't a physical thing: it's a number the kernel uses to decide which process gets the incoming bytes.

```
           one machine (WSL)                                      ports on 127.0.0.1
 ┌──────────────────────────────────────────────────────────┐
 │                                                          │      ┌───────┐
 │  client: kiln submit   ──── 1. connect ───────────────────┼─────▶│ 7340  │──┐
 │                        ──── 2. request: {"type":"submit"} ──▶   └───────┘  │
 │                        ◀─── 3. response: {"type":"ok",…}  ───                ▼
 │                                                          │      ┌───────────────────────┐
 │                                                          │      │ server: kiln master   │
 │                                                          │      │ (runs until stopped;  │
 │  client: curl  ── HTTP ─────────────────────────────────┼─────▶│  K5 adds a gateway    │
 │                                                          │ 7341 │  server on 7341)      │
 │                                                          │      └───────────────────────┘
 └──────────────────────────────────────────────────────────┘
   127.0.0.1 = "localhost" = this same machine. Nothing leaves the computer.
```

## See it for yourself

The snippet [`snippets/what-is-a-server_1.py`](snippets/what-is-a-server_1.py) runs a tiny server and a tiny client in one script. The server answers every request with the same text in upper case:

```bash
(wsl) $ python3.15 docs/concepts/snippets/what-is-a-server_1.py
```

```text
server: listening on 127.0.0.1:34615
server: got b'hello kiln' from a client
client: sent b'hello kiln', received b'HELLO KILN'
server: got b'run job 42' from a client
client: sent b'run job 42', received b'RUN JOB 42'
server: closed
```

Line by line:
- `listening on 127.0.0.1:34615`: the server asked for **port 0**, which means "kernel, pick any free port". It got 34615. Run it again and you'll see another number. Kiln's tests use the same trick so they never collide with a real server.
- `got b'hello kiln'`: the `b'…'` means these are **bytes**, not text. Networks carry bytes (see the Python note [bytes-vs-str](../python/bytes-vs-str.md)).
- Each `client:` line is a full round trip: connect, send the request, receive the response.
- A real server would loop forever. This one stops after two clients so the demo ends.

A listening server is visible to the whole machine. [`snippets/what-is-a-server_2.py`](snippets/what-is-a-server_2.py) opens a listening socket and asks the `ss` tool ("socket statistics") to list it:

```text
our server is listening on port 33317
State  Recv-Q Send-Q  Local Address:Port  Peer Address:Port
LISTEN 0      128         127.0.0.1:33317      0.0.0.0:*
closed; the port is free again
```

`LISTEN` means "waiting for connections". `Send-Q 128` is the size of the queue of connections that have arrived but haven't been accepted yet (the *backlog*).

## How it really works

**Sockets.** A network connection, seen from inside a process, is a **socket**: a file descriptor you can read bytes from and write bytes to (see [files-and-file-descriptors](files-and-file-descriptors.md)). A server makes one special socket, the **listening socket**, in four steps (each a system call; see `man 2 <name>`):

| Step | Python | What it does |
|---|---|---|
| 1 | `socket.socket(AF_INET, SOCK_STREAM)` | create an IPv4 **TCP** socket |
| 2 | `.bind(("127.0.0.1", 7340))` | claim an address and port |
| 3 | `.listen()` | start accepting connection attempts into a queue |
| 4 | `.accept()` | take one waiting connection, which gives a **new** socket for talking to that client |

The client side does `socket.create_connection((host, port))`, then sends and receives.

**TCP** (Transmission Control Protocol) delivers a reliable, ordered **stream of bytes** in both directions. "Stream" matters: TCP doesn't keep message boundaries. If a client sends two messages quickly, the server might receive them glued together, or one cut in half. That's why Kiln wraps each message in a *frame* with its length in front ([spec/02 §1](../spec/02-protocol.md#1-why-framing-is-needed-read-this-if-tcp-is-new)). The demo got away without framing only because each client sent one tiny message and waited.

**Ports and addresses.** An IP address picks the *machine*, and a port (1–65535) picks the *program* on it. **`127.0.0.1`**, also called **localhost**, is the "loopback" address: traffic to it never leaves the computer. Kiln binds only to `127.0.0.1` on purpose ([ADR-021](../decisions.md#adr-021)), so nothing outside your machine can reach it.

**Request / response.** The client sends a request, the server does work and sends a response. Kiln's requests are JSON objects such as `{"type": "submit", "task": "add", "args": [2, 3]}` ([spec/02 §3](../spec/02-protocol.md#3-client--master-messages-kp1-client-channel)).

**Why servers run "forever".** Starting a process and loading code takes time, from milliseconds to seconds. A server pays that cost once, then answers thousands of requests cheaply. The flip side is that anything a server does slightly wrong (holding memory, leaving fds open, blocking for a moment) adds up over days. That's why Kiln has worker recycling (K1), leak hunting (K6) and an event-loop watchdog (K5).

**Where Kiln fits** ([spec/01 §1](../spec/01-architecture.md#1-the-big-picture)): the *master* is the server that clients talk to. It doesn't run jobs itself. It hands them to *worker* processes over private connections and sends back the results. That's the same shape as gunicorn (web requests) and Celery (background jobs).

## How Kiln uses it

- From K1: the master listens on `master.bind = "127.0.0.1:7340"` ([spec/05](../spec/05-configuration-and-cli.md#master)), and `kiln submit` is the client ([spec/02](../spec/02-protocol.md)).
- From K5: the gateway listens on `127.0.0.1:7341` and speaks HTTP.
- In K0 already: [`src/kiln/doctor.py`](../../src/kiln/doctor.py) check D18 tries to bind ports 7340 and 7341, to warn you early if another program is already using them.
- Tests always bind to port 0, so parallel test runs never fight over a port ([spec/09 §4.3](../spec/09-testing-strategy.md#43-ports-bind-to-port-0)).

## Common misconceptions

- ❌ **Myth:** a server is a special, powerful computer.
  ✅ **Reality:** "server" describes a *role*: a process that waits for and answers requests. Your laptop runs several right now. In the `ss -ltn` output on this WSL, there was already one listening on port 53 (DNS).
- ❌ **Myth:** one `send` on one side equals one `recv` on the other.
  ✅ **Reality:** TCP is a byte stream with no message boundaries. Sends can be merged or split. Protocols add framing to mark messages (Kiln's KP/1 does).
- ❌ **Myth:** `localhost` traffic goes out through the network card.
  ✅ **Reality:** `127.0.0.1` is handled entirely inside the kernel's loopback interface. No packet leaves the machine.
- ❌ **Myth:** a server handles each client in a separate program.
  ✅ **Reality:** designs vary. One process can juggle many connections (event loops: Kiln's master, asyncio), or a pool of pre-started processes can share the work (pre-fork servers: gunicorn, and Kiln's workers).

## Check yourself

1. Two programs both try to `bind` to `127.0.0.1:7340`. What happens to the second?
   <details><summary>Answer</summary>

   It fails with `OSError: [Errno 98] Address already in use`. Only one listening socket can own an address and port at a time, which is why `kiln doctor` checks D18 warns when 7340 or 7341 is taken.

   </details>
2. Why did the demo server bind to port 0?
   <details><summary>Answer</summary>

   Port 0 asks the kernel for any free port, so the demo (and Kiln's tests) can never clash with something already running. The program then reads the real port with `getsockname()`.

   </details>
3. Predict: a client sends `b"ab"` then immediately `b"cd"`. The server calls `recv(1024)` once. What could it receive?
   <details><summary>Answer</summary>

   Possibly `b"abcd"` (merged), possibly just `b"ab"` or even `b"a"`, with the rest arriving in later `recv` calls. TCP is a byte stream. That's exactly why Kiln adds length-prefixed framing (spec/02 §1).

   </details>
4. Why is Kiln only reachable from your own machine?
   <details><summary>Answer</summary>

   It binds to `127.0.0.1` (localhost), which only accepts connections from the same machine. Binding elsewhere requires `allow_non_loopback = true` (ADR-021).

   </details>

## Go deeper

- [Python docs: Socket Programming HOWTO](https://docs.python.org/3/howto/sockets.html), a friendly introduction.
- `man 7 tcp`, `man 2 bind`, `man 2 listen`, `man 2 accept`.
- `man 8 ss`, for inspecting sockets on Linux.
- Ryan Tomayko, "I like Unicorn because it's Unix": the pre-fork server design Kiln follows (linked from the K1 spec).
