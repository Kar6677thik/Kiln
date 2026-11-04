# fork(): Making a Copy of a Running Process

> **Milestone:** K1 · **Read before:** [what-is-a-process](what-is-a-process.md), [files-and-file-descriptors](files-and-file-descriptors.md) · **Reading time:** ~15 min
> **Terms introduced:** **fork**, **copy-on-write (CoW)**, **os._exit**, **inherited file descriptor**, **signal disposition**

## One-sentence summary

`os.fork()` makes an almost exact copy of the running process. After that line *two* processes run the same code, and the only way each one can tell which copy it is: the value `fork()` returned.

## Why you should care

Kiln's master creates every worker with `fork()` ([spec/04 §6](../spec/04-supervisor.md#6-start-methods-k2)), and gunicorn and Celery do the same. Several of Kiln's real-world incidents come straight from what `fork()` copies and what it doesn't:

- Instagram's memory problem (research §2.1, Kiln K3) is about memory that `fork()` *shares* between parent and child until someone writes to it.
- Python 3.14 switched Linux's `multiprocessing` default away from `fork` because forking a process with threads can deadlock (research §2.9, Kiln K2). See [fork-and-threads](fork-and-threads.md).
- A forked child that doesn't exit the right way runs the parent's cleanup code a second time. Kiln has a rule just for this ([ADR-028](../decisions.md#adr-028)).

If you understand this page, most of K1–K3 will make sense.

## The mental model

**Analogy:** a photocopier for a person in the middle of a task. You're halfway through reading a recipe when you're photocopied. Now there are two of you, standing in identical kitchens, both about to read step 4. Each can then carry on differently.

**Where the analogy fails:** the two copies *aren't* fully separate kitchens at first. The kernel lets them share the same memory pages until one of them writes to a page (copy-on-write, below). And some things are shared for real, forever: open files and sockets point at the *same* underlying object in both processes.

```
 before fork():                 after fork():
 ┌─────────────────────┐        ┌─────────────────────┐   ┌─────────────────────┐
 │ process (pid 100)   │        │ parent (pid 100)    │   │ child  (pid 101)    │
 │ memory: counter=10  │  ───▶  │ fork() returned 101 │   │ fork() returned 0   │
 │ fds: 0 1 2 5        │        │ memory: counter=10  │   │ memory: counter=10  │ (a copy)
 │ threads: 1          │        │ fds: 0 1 2 5 ───────┼───┼─▶ fds: 0 1 2 5      │ (same files!)
 └─────────────────────┘        └─────────────────────┘   └─────────────────────┘
                                 both continue on the line right after os.fork()
```

## See it for yourself

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/fork_1.py
```

```text
before fork: this line sits in the output buffer, not yet written
child:  fork() returned 0, my counter = 11
before fork: this line sits in the output buffer, not yet written
parent: fork() returned a pid > 0: True, my counter = 10
```

Line by line:
- The child saw `fork()` return **0**. The parent saw the child's pid (a positive number). This is the *only* difference between them right after the fork, and `if pid == 0:` is how code chooses a path.
- The child changed `counter` to 11, but the parent still sees **10**. Each process has its own copy of memory.
- **"before fork" printed twice.** When stdout goes to a pipe or file (as here, where the output was captured), Python keeps printed text in a memory buffer and writes it out later. That buffer was copied into the child too, so both processes eventually wrote it. In an interactive terminal Python flushes after every line, so you'd see the line once. That's why Kiln's fork launcher flushes stdout, stderr and every log handler **right before** forking.

The second snippet shows why the *way* a forked child exits matters:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/fork_2.py
```

```text
  atexit handler ran in: child that called sys.exit()
child using sys.exit() has exited
child using os._exit() has exited
parent is about to exit normally
  atexit handler ran in: parent
```

The parent registered an `atexit` handler: a function Python runs when the interpreter shuts down normally. The child that left with `sys.exit()` ran a normal shutdown, so it ran the **parent's** handler. The child that left with `os._exit()` skipped it. In Kiln a parent handler might delete the master's pidfile, so a worker running it would be a real bug.

## How it really works

- **The system call.** `os.fork()` calls the C library's `fork()`, which on Linux uses the `clone` system call (`man 2 fork`). The kernel creates a new process with a new pid, and makes the parent its parent.
- **Memory: copy-on-write (CoW).** Copying gigabytes of memory would be slow, so the kernel doesn't. It maps the *same* physical memory pages into both processes and marks them read-only. The first time either process **writes** to a page, the CPU traps into the kernel, which copies just that 4 KiB page for the writer. Pages nobody writes stay shared forever. K3 is about how CPython writes to pages you'd expect to stay untouched.
- **File descriptors are duplicated, not reopened.** The child gets the same fd numbers, pointing at the same open files and sockets. If the child closes fd 5 it only closes *its* copy, but data written to a shared socket by either one goes to the same place. That's why Kiln's child closes every fd it doesn't need (the listener, other workers' channels…) first thing ([spec/04 §6 rule 1](../spec/04-supervisor.md#6-start-methods-k2)).
- **Signal dispositions are copied.** A **signal disposition** is "what this process does when signal X arrives": ignore it, run a handler, or the default action. The child inherits the parent's, so Kiln's worker resets them at startup ([spec/04 §3.1](../spec/04-supervisor.md#31-signals-in-workers)).
- **Only the calling thread is copied.** If the parent had 5 threads, the child has 1. See [fork-and-threads](fork-and-threads.md).
- **`os._exit(code)` vs `sys.exit(code)`.** `sys.exit` raises `SystemExit`, which unwinds the call stack (running `finally:` blocks), then runs `atexit` handlers, flushes buffers and tears down modules. `os._exit` calls the `_exit` system call straight away: no unwinding, no handlers, no flushing. A forked child is running *inside the master's call stack*, so unwinding would run the master's `finally:` blocks. That's why forked children must use `os._exit` after flushing their own output by hand.
- **Random numbers.** The child has a copy of the parent's random-number state, so without help both would produce the same "random" numbers. Python registers an `os.register_at_fork` hook that reseeds the `random` module in the child automatically. Kiln checks this in a test (gate G1.3).

## How Kiln uses it

- [`launch/fork.py` → `fork_worker`](../../src/kiln/launch/fork.py) flushes the buffers, creates the worker's socketpair, calls `os.fork()`, and splits into the parent branch (keep one end of the socketpair) and the child branch (close inherited fds, run the worker). Comments there are tagged `CONCEPT(fork)`.
- [`worker/main.py` → `finalize_and_exit`](../../src/kiln/worker/main.py) is how every forked worker ends: flush the logs, flush stdout/stderr, `os._exit(code)` ([ADR-028](../decisions.md#adr-028)).
- The arbiter asserts it has exactly one thread before every fork ([ADR-006](../decisions.md#adr-006)).

## Common misconceptions

- ❌ **Myth:** "`fork()` starts the program again from the top, like running it twice."
  ✅ **Reality:** both processes continue from the line *after* `fork()`, with all variables as they were. Starting fresh is a different start method, *spawn*, covered in K2.
- ❌ **Myth:** "After fork, the child's memory is a full copy, so forking a 1 GB process uses 1 GB more."
  ✅ **Reality:** pages are shared copy-on-write. Extra memory is only used for pages someone writes to. How many get written is exactly what K3 measures.
- ❌ **Myth:** "Calling `sys.exit()` in the child only affects the child, so it's fine."
  ✅ **Reality:** it only *ends* the child, but on the way out the child runs the cleanup code it inherited from the parent (`atexit`, `finally:`, buffer flushes), as `fork_2.py` showed.
- ❌ **Myth:** "The child gets copies of the parent's open files."
  ✅ **Reality:** it gets copies of the *file descriptors*, which point at the **same** open files and sockets.

## Check yourself

1. After `pid = os.fork()`, how many processes run the next line? What is `pid` in each?
   <details><summary>Answer</summary>

   Two. In the child `pid == 0`; in the parent `pid` is the child's process id, a positive number. (If `fork` fails, it raises `OSError` and no child exists.)

   </details>
2. Predict the output: `x = [1]`, then fork; the child does `x.append(2)`, prints `x`, and exits; the parent waits, then prints `x`.
   <details><summary>Answer</summary>

   The child prints `[1, 2]` and the parent prints `[1]`. The list was copied with the rest of memory, so changing it in the child doesn't affect the parent.

   </details>
3. Why does Kiln's fork launcher flush stdout, stderr and the log handlers right before calling `os.fork()`?
   <details><summary>Answer</summary>

   Anything still sitting in an output buffer gets copied into the child, so both processes would eventually write it and you'd see it twice. Flushing first leaves both buffers empty.

   </details>
4. Why must a forked Kiln worker end with `os._exit()` and not by returning or `sys.exit()`?
   <details><summary>Answer</summary>

   The child runs inside a copy of the master's call stack and has the master's `atexit` handlers. Returning or `sys.exit()` would run the master's `finally:` blocks and handlers, for example deleting the pidfile or closing master sockets. `os._exit` ends the process immediately, so the worker flushes its own output first and then calls it.

   </details>

## Go deeper

- `man 2 fork`: the exact list of what is and isn't inherited.
- [`os.fork` in the Python docs](https://docs.python.org/3/library/os.html#os.fork) and [`os.register_at_fork`](https://docs.python.org/3/library/os.html#os.register_at_fork).
- Ryan Tomayko, *I like Unicorn because it's Unix*: a pre-fork server explained with these same ideas.
- CPython source: `Modules/posixmodule.c` (`os_fork_impl`), which shows the at-fork hooks being run.
