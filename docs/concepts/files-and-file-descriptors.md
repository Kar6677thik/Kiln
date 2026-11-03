# Files and File Descriptors

> **Milestone:** K0 · **Read before:** [what-is-a-process](what-is-a-process.md) · **Reading time:** ~15 min
> **Terms introduced:** **file descriptor (fd)**, **stdin / stdout / stderr**, **redirection**, **pipe**, **file object**, **"everything is a file"**

## One-sentence summary

When a process opens a file, a socket or a pipe, the kernel hands it a small integer called a **file descriptor (fd)**, a ticket number for "that open thing". Every later read or write uses the ticket.

## Why you should care

Everything Kiln's processes say to each other travels through file descriptors: the master's listening socket, each worker's private channel, the signal "wake-up" pipe, and later the forkserver passing an open fd from one process to another ([spec/01 §3](../spec/01-architecture.md#3-connections-channels)). Several of the nastiest bugs in K1 and K2 are fd bugs:

- A forked worker accidentally keeps the master's listening socket open, so the socket never really closes.
- A worker inherits the master's signal pipe, so the worker's signals wake the master by mistake (spec/04 §3.1).
- An fd isn't closed, and a long-running server slowly runs out of them.

To follow any of that, you need to see fds as concrete numbers that you can list and inspect, and this primer does exactly that.

## The mental model

**Analogy:** a cloakroom. You hand in a coat (open a file) and get back a numbered ticket (the fd). From then on you don't describe the coat; you say "ticket 3, please". When you're done you return the ticket (close), and the number can be given to someone else.

**Where the analogy fails:** every process has its *own* cloakroom with its own ticket numbers, so fd 3 in one process has nothing to do with fd 3 in another. And sometimes two tickets, even in two different processes, point at the *same* coat. That happens after `fork()` (K1) and with fd passing (K2), and it's the source of the bugs above.

```
 process (pid 2003)                           kernel
 ┌───────────────────────────┐        ┌─────────────────────────────────────────┐
 │ fd table                  │        │ open "things"                           │
 │  0  stdin  ───────────────┼───────▶│ pipe / terminal (input)                 │
 │  1  stdout ───────────────┼───────▶│ pipe / terminal (output)                │
 │  2  stderr ───────────────┼───────▶│ pipe / terminal (errors)                │
 │  3  ──────────────────────┼───────▶│ file /tmp/…/notes.txt  (position: 0)    │
 │  4  ──────────────────────┼───────▶│ TCP socket 127.0.0.1:7340  (K1)         │
 └───────────────────────────┘        └─────────────────────────────────────────┘
   Python code holds objects (file, socket) that remember their fd number:
   fh = open(...)  →  fh.fileno() == 3
```

## See it for yourself

The snippet [`snippets/files-and-file-descriptors_1.py`](snippets/files-and-file-descriptors_1.py) opens a file and lists every fd the process has:

```bash
(wsl) $ python3.15 docs/concepts/snippets/files-and-file-descriptors_1.py
```

Real output, run through `wsl.exe` from Windows (which is why 0, 1 and 2 are *pipes*; see below):

```text
stdin, stdout, stderr fds: 0 1 2
open() gave a file object; its fd is 3
  fd  0 -> pipe:[229931]
  fd  1 -> pipe:[229932]
  fd  2 -> pipe:[229933]
  fd  3 -> /tmp/tmptusmpqaf/notes.txt
  fd  4 -> (closed while listing)
  fd  5 -> /dev/ptmx
  fd 10 -> /dev/ptmx
after the with-block, is fd still open? False
written directly to file descriptor 1
```

Line by line:
- `0 1 2`: every process starts with three fds already open: **stdin** (0, input), **stdout** (1, normal output) and **stderr** (2, error output). `print()` writes to fd 1.
- `its fd is 3`: `open()` got the **lowest free number**, which was 3.
- `fd 0/1/2 -> pipe:[…]`: here the snippet's input and output were connected to *pipes* leading back to the program that launched it (`wsl.exe`). Run in an interactive WSL terminal, they'd point to a terminal device such as `/dev/pts/0`. The process doesn't care which; it just writes to fd 1.
- `fd 3 -> …/notes.txt`: the file we opened.
- `fd 4 -> (closed while listing)`: a surprise! To list the folder `/proc/self/fd`, Python itself opened that folder as fd 4, then closed it before we could follow the link. The act of looking added an fd.
- `fd 5, fd 10 -> /dev/ptmx`: fds the process **inherited** from whoever started it (WSL's own plumbing). Nobody in our code opened them. That's a gentle preview of the K1 rule "a child inherits its parent's open fds unless something closes them".
- `is fd still open? False`: leaving the `with` block closed the file, and fd 3 disappeared.
- The last line was written with `os.write(1, …)`, straight to fd 1, without `print()`.

### Redirection in the shell

The shell can point a command's fd 1 and fd 2 at files. `>` redirects fd 1 and `2>` redirects fd 2. Real output:

```text
$ ls README.md no-such-file > /tmp/out.txt 2> /tmp/err.txt; echo "exit code: $?"
exit code: 2
--- contents of out.txt (stdout, fd 1):
README.md
--- contents of err.txt (stderr, fd 2):
ls: cannot access 'no-such-file': No such file or directory
```

`ls` didn't know or care where its output went. It wrote normal results to fd 1 and complaints to fd 2. That separation is why Kiln logs go to **stderr** while command results go to **stdout** ([spec/06 §1.5](../spec/06-observability.md#15-where-logs-go)): you can redirect one without losing the other.

You can also list a process's fds from the shell. Real output, with `ls` listing its *own* fds:

```text
$ ls -l /proc/self/fd
lr-x------ 1 Megatron Megatron 64 Nov  3 15:57 0 -> pipe:[228147]
l-wx------ 1 Megatron Megatron 64 Nov  3 15:57 1 -> pipe:[228148]
lrwx------ 1 Megatron Megatron 64 Nov  3 15:57 10 -> /dev/ptmx
l-wx------ 1 Megatron Megatron 64 Nov  3 15:57 2 -> pipe:[228149]
lr-x------ 1 Megatron Megatron 64 Nov  3 15:57 3 -> /proc/2085/fd
lrwx------ 1 Megatron Megatron 64 Nov  3 15:57 5 -> /dev/ptmx
```

Same story: fd 3 is `ls` reading its own fd folder, and fds 5 and 10 are inherited. The permission letters at the start show direction: `r-x` means opened for reading (stdin), `-wx` for writing (stdout, stderr) and `rwx` for both.

## How it really works

**The system calls.** Python's friendly functions sit on top of a handful of kernel calls:

| Python | System call (see `man 2 <name>`) | What happens |
|---|---|---|
| `open("f")` | `openat` | the kernel opens the file and returns the lowest unused fd |
| `fh.read()` | `read(fd, …)` | copies bytes from the open thing into the process |
| `print(...)` / `os.write(1, b"…")` | `write(1, …)` | copies bytes out |
| `fh.close()` / end of `with` | `close(fd)` | the ticket is returned, and the number becomes free |
| `socket.socket()` | `socket` | yes, sockets are fds too |

**A file object wraps an fd.** `open()` returns a Python *file object*, which adds buffering, text decoding and a nice API, and it remembers its fd (`fh.fileno()`). Closing the object closes the fd. The Python note [context-managers](../python/context-managers.md) explains why `with open(...)` guarantees that.

**"Everything is a file".** This classic Unix motto means that many different things are presented through the same fd interface: regular files, terminals, **pipes** (one-way byte channels between processes), network sockets, and even `/proc` entries. One `read`/`write`/`close` vocabulary works for all of them. That's why Kiln's event loop (K1) can wait on the listening socket, the worker channels and the signal pipe all at once: to the kernel, they are all fds.

**Limits.** A process may have only so many fds open at once. `ulimit -n` shows the limit. On this WSL it prints `10240` (the "soft" limit; `ulimit -Hn`, the hard ceiling a process may raise itself to, prints `1048576`). Many Linux systems default to 1024. A server that forgets to close connections eventually fails with `OSError: [Errno 24] Too many open files`. Kiln's tests check for leaked fds (spec/09 §4.6).

**Inheritance.** When a process creates a child, the child starts with *copies of the parent's fd table*, unless an fd is marked "close-on-exec". Since Python 3.4 (PEP 446), fds that Python opens are non-inheritable by default for new programs (`exec`), but `fork()` still copies everything. K1 and K2 build on exactly this.

## How Kiln uses it

- [`src/kiln/doctor.py`](../../src/kiln/doctor.py) opens and reads files under `/proc` and `/sys`: `/proc/meminfo` (RAM), `/proc/sys/kernel/yama/ptrace_scope`, `/proc/self/smaps_rollup` (detailed memory, needed by K3), and `/proc/mounts` (to tell ext4 from the slow 9P mount). Each one is an `open()` → fd → `read` → `close`.
- [`src/kiln/bench/records.py`](../../src/kiln/bench/records.py) writes experiment records **atomically**: it writes to a temporary file, closes the fd, then renames it into place, so a crash never leaves half a record ([spec/10 §2.1](../spec/10-measurement-methodology.md#21-field-rules)).
- From K1 on, the master keeps a registry of every fd it opens, so that forked workers can close the ones they must not hold ([spec/04 §6](../spec/04-supervisor.md#6-start-methods-k2), rule 1).

## Common misconceptions

- ❌ **Myth:** a file descriptor is the file.
  ✅ **Reality:** it's only a number in *one process's* table that refers to an open file (or socket, or pipe). The same file opened twice gets two fds, each with its own read position.
- ❌ **Myth:** fd 3 is always the first file I open.
  ✅ **Reality:** you get the *lowest free* number, and inherited fds or library internals may already hold 3, 4 or 5. The snippet showed fds 5 and 10 inherited from WSL.
- ❌ **Myth:** when my Python object is garbage, the file is closed right away, so `close()` doesn't matter.
  ✅ **Reality:** CPython often closes it promptly, but not always (reference cycles, other interpreters), and Python warns with `ResourceWarning`. Close explicitly, ideally with `with`.
- ❌ **Myth:** stderr is only for errors, so it doesn't matter where it goes.
  ✅ **Reality:** stderr is the channel for *diagnostics*, logs included. Kiln deliberately sends logs to stderr so that stdout stays clean for results.

## Check yourself

1. Predict: a fresh Python process opens two files, closes the first, then opens a third. What fd number does the third get, assuming nothing else was open?
   <details><summary>Answer</summary>

   The first two files get 3 and 4. Closing the first frees 3, and the kernel always hands out the *lowest* free number, so the third file gets **3** again.

   </details>
2. `python3.15 app.py > out.txt` and the program crashes with a traceback. Where does the traceback appear?
   <details><summary>Answer</summary>

   On the screen (stderr, fd 2). `>` only redirected fd 1 into `out.txt`, and Python writes tracebacks to stderr. To capture both, add `2> err.txt`, or `2>&1` to send fd 2 wherever fd 1 goes.

   </details>
3. In the snippet's output, why did fd 4 show "(closed while listing)"?
   <details><summary>Answer</summary>

   `os.listdir("/proc/self/fd")` opened that directory, which used the lowest free fd (4), to read its entries, then closed it. By the time the loop asked "where does fd 4 point?", it was already gone. Observing a process can change what you observe.

   </details>
4. Why can the master's event loop (K1) wait on sockets and a pipe with one mechanism?
   <details><summary>Answer</summary>

   Because they're all file descriptors ("everything is a file"). The kernel's waiting mechanism (`epoll`, used through Python's `selectors`) takes a list of fds and wakes the process when any of them is ready, whatever kind of object is behind each one.

   </details>

## Go deeper

- `man 2 open`, `man 2 read`, `man 2 close`: the real system calls.
- `man 5 proc`, entries `/proc/[pid]/fd` and `/proc/[pid]/fdinfo`.
- [PEP 446: Make newly created file descriptors non-inheritable](https://peps.python.org/pep-0446/).
- [Python docs: `os` file descriptor operations](https://docs.python.org/3/library/os.html#file-descriptor-operations).
- [Python note: context managers](../python/context-managers.md), for why `with open(...)` is the safe pattern.
