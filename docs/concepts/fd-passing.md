# Passing File Descriptors Between Processes

> **Milestone:** K2 · **Read before:** [files-and-file-descriptors](files-and-file-descriptors.md), [sockets-and-socketpairs](sockets-and-socketpairs.md) · **Reading time:** ~14 min
> **Terms introduced:** **inheritable fd**, **PEP 446**, **SCM_RIGHTS**, **ancillary data**, **fd FIFO**

## One-sentence summary

An open file or socket can reach another process in two ways: **inheritance**, where a new program keeps fds that were deliberately marked **inheritable**, and **fd passing**, where an fd is sent through a Unix socket as **ancillary data** (`SCM_RIGHTS`) and arrives as a *new number* pointing at the *same* open object.

## Why you should care

Every Kiln worker talks to the master through its own socketpair. Under fork the child simply inherits its end. Under the other two start methods there's work to do:
- **spawn:** a fresh program would see *none* of the master's fds, because Python makes all fds non-inheritable by default (**PEP 446**). The spawn launcher marks exactly one fd, the worker's channel, as inheritable.
- **forkserver:** the forkserver was started long before this worker's socketpair existed, so the master has to *send* the channel fd to the forkserver over their control socket. The forkserver then forks a worker that inherits it.

Get this wrong and workers either can't talk to the master, or they leak fds they shouldn't have (the K1 fd-hygiene rule).

## The mental model

**Analogy:** fds are like numbered coat-check tickets for open files. Inheritance is a parent handing their whole ticket wallet to a child, minus every ticket stamped "do not pass on". Fd passing is mailing a ticket to someone else. The coat-check desk (the kernel) issues them a *new* ticket number for the *same* coat.

**Where the analogy fails:** after passing, *both* processes hold a ticket for the same coat. Closing one ticket doesn't close the file for the other. The object goes away only when the last fd for it is closed.

```
 inheritance across exec (spawn)                 SCM_RIGHTS over a Unix socket (forkserver)
 ───────────────────────────────                 ──────────────────────────────────────────
 master fds: 3 4 7 9 12                          master fd 9 ──sendmsg(data, [SCM_RIGHTS: 9])──▶
   set_inheritable(12, True)                                  kernel duplicates the open object
   posix_spawn(python -m kiln.worker --fd 12)    forkserver ◀── recvmsg ── new fd 6 (same socket!)
 worker fds: 0 1 2 12   ← only the marked one    forkserver: fork() → worker inherits fd 6
```

## See it for yourself

**1. Inheritance (PEP 446).** [`snippets/fd-passing_1.py`](snippets/fd-passing_1.py) makes a pipe, spawns a fresh Python and asks it to write to the pipe's fd. It does this twice, first without and then with `set_inheritable`:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/fd-passing_1.py
```

```text
pipe fds 3, 4; inheritable by default? False
spawning a fresh python with the fd marked inheritable=False:
  child: fd 4 fails: Bad file descriptor
spawning a fresh python with the fd marked inheritable=True:
  child: fd 4 works
```

New fds aren't inheritable. A fresh program sees fd 4 as "Bad file descriptor" (EBADF) until we mark it.

**2. Passing (SCM_RIGHTS).** This is gate G2.3. [`snippets/fd_passing.py`](snippets/fd_passing.py) opens a file in the parent and *sends the open file* to a child over a socketpair:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/fd_passing.py
```

```text
parent: opened the file as fd 4
child : got message b'here is a file' and fd 8
child : reading it gives b'hello from a file the child never opened\n'
```

The child never opened the file and doesn't know its name. It received fd **8**, a different number from the parent's 4, and reading it works. Same open file, new number.

## How it really works

- **PEP 446 (Python 3.4).** Every fd Python creates gets the `O_CLOEXEC` flag ("close on exec"). When a process `exec`s a new program, the kernel closes those fds. `os.set_inheritable(fd, True)` clears the flag for one fd. `posix_spawn` and `subprocess` both end in `exec`, so only the inheritable fds survive. `fork` alone doesn't `exec`, so a forked child keeps *everything*. That's why Kiln's fork launcher has to close fds by hand.
- **SCM_RIGHTS.** A Unix-domain socket can carry **ancillary data** alongside the normal bytes, in `sendmsg(2)`/`recvmsg(2)`. A control message of type `SCM_RIGHTS` holds a list of fds. The kernel doesn't copy the *numbers*. It installs new fd table entries in the receiver that point at the same open file descriptions, picking the lowest free numbers in the receiver. Python wraps this as `socket.send_fds(sock, [data], [fds])` and `socket.recv_fds(sock, bufsize, maxfds)`.
- **Pairing fds with frames.** Ancillary data travels with the bytes of *that* `sendmsg` call. A stream socket can still split or merge those bytes on receipt, so the receiver can't assume "one recv = one frame". Kiln's forkserver therefore puts received fds in a **FIFO queue** and pops exactly one fd for each decoded `spawn_worker` frame (K2 spec §5.4). A `spawn_worker` frame with no queued fd is a protocol error.

## How Kiln uses it

- [`src/kiln/launch/spawn.py`](../../src/kiln/launch/spawn.py) calls `os.set_inheritable(fd, True)` on the worker's channel only, then `os.posix_spawn(... "--fd", str(fd) ...)`. [`src/kiln/worker/__main__.py`](../../src/kiln/worker/__main__.py) turns that number back into a socket.
- [`src/kiln/launch/forkserver.py`](../../src/kiln/launch/forkserver.py) sends `spawn_worker` frames with `socket.send_fds`, and [`src/kiln/launch/forkserver_main.py`](../../src/kiln/launch/forkserver_main.py) receives them with `recv_fds` into the fd FIFO.
- Code tag: `CONCEPT(fd-passing)`.

## Common misconceptions

- ❌ **Myth:** "Sending fd 9 to another process gives them fd 9."
  ✅ **Reality:** they get whatever number is free on *their* side (8 in the demo). Only the underlying open file is shared.
- ❌ **Myth:** "A child process gets all its parent's open files."
  ✅ **Reality:** a *forked* child does. A program started with exec (spawn, `subprocess`) gets only stdin, stdout, stderr and fds explicitly marked inheritable (PEP 446).
- ❌ **Myth:** "Once I've sent an fd, I should keep my copy open for the receiver."
  ✅ **Reality:** the receiver holds its own reference. The sender can close its copy right away, and Kiln's master does.

## Check yourself

1. Why does Kiln's spawn launcher call `os.set_inheritable` on exactly one fd?
   <details><summary>Answer</summary>

   All Python-created fds are close-on-exec (PEP 446), so a spawned worker would get none of them. Marking only the channel gives it the one fd it needs, and none of the master's listener or client sockets.

   </details>
2. Predict: the master sends its fd 12 to the forkserver, which has fds 0–5 open. Which number will the forkserver most likely see?
   <details><summary>Answer</summary>

   6, the lowest free number on the receiver's side. The number 12 means nothing there.

   </details>
3. Why does the forkserver queue received fds instead of assuming "this recv's fd belongs to this recv's frame"?
   <details><summary>Answer</summary>

   A stream socket can split one sent message across several recvs, or merge several into one. The fd arrives with the *start* of its bytes, but the frame it belongs to may complete later. A FIFO keeps the fds in order, and each complete `spawn_worker` frame takes the next one.

   </details>

## Go deeper

- [PEP 446: Make newly created file descriptors non-inheritable](https://peps.python.org/pep-0446/).
- [Python docs: `socket.send_fds` / `recv_fds`](https://docs.python.org/3.15/library/socket.html#socket.send_fds).
- `man 7 unix` (section "Ancillary messages", `SCM_RIGHTS`), `man 2 sendmsg`.
- CPython source: `Lib/multiprocessing/reduction.py` (`sendfds`, `recvfds`).
