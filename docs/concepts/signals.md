# Signals: Tiny Notifications Between Processes

> **Milestone:** K1 · **Read before:** [what-is-a-process](what-is-a-process.md), [fork](fork.md) · **Reading time:** ~15 min
> **Terms introduced:** **signal**, **signal handler**, **default action**, **SIG_IGN**, **wakeup fd**, **process group**

## One-sentence summary

A **signal** is a small numbered notification that the kernel delivers to a process ("please stop", "a child of yours ended", "reload"). The process either runs its own handler, ignores it, or lets the kernel's default action happen, which is often "die".

## Why you should care

Signals are how you control a pre-fork server from the outside: `kill -HUP <pid>` reloads gunicorn, Ctrl-C stops it, and the kernel sends `SIGCHLD` when a worker dies. They're also how processes *get killed*. The kernel's OOM killer sends `SIGKILL`, and Back Market's Celery workers died that way with only "worker lost" in the logs (research §2.7). Kiln's master reacts to eight signals ([spec/04 §3](../spec/04-supervisor.md#3-signals-the-master-handles)), and its workers set theirs up deliberately ([spec/04 §3.1](../spec/04-supervisor.md#31-signals-in-workers)).

## The mental model

**Analogy:** a doorbell. It carries no message, only *which* bell rang. When it rings you can answer (a handler), pretend you didn't hear it (ignore), or let the house rules decide (the default action). One bell, `SIGKILL`, isn't a doorbell at all: the landlord walks in and you're evicted. You can't catch, ignore or delay it.

**Where the analogy fails:** a real doorbell can ring ten times and you hear ten rings. Standard signals don't queue: if the same signal arrives twice before it's handled, the process may only see it once. And the "answer" happens at an awkward moment (below).

| Signal | Number on Linux | Default action | Typical sender | What Kiln's master does |
|---|---|---|---|---|
| `SIGINT` | 2 | terminate | Ctrl-C in a terminal | graceful shutdown (2nd: fast) |
| `SIGTERM` | 15 | terminate | `kill`, systemd, Kubernetes | graceful shutdown |
| `SIGQUIT` | 3 | terminate + core dump | Ctrl-\ | immediate shutdown |
| `SIGHUP` | 1 | terminate | `kill -HUP` | reload workers |
| `SIGTTIN` / `SIGTTOU` | 21 / 22 | stop | `kill -TTIN` | one more / one fewer worker |
| `SIGUSR1` | 10 | terminate | you | log a status dump |
| `SIGCHLD` | 17 | ignore | the kernel, when a child exits | reap children |
| `SIGKILL` | 9 | terminate, **can't be caught** | the OOM killer, `kill -9` | (the master uses it on stuck workers) |

## See it for yourself

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/signals_1.py
```

```text
handler saw: ['SIGUSR1']
SIGUSR2 was ignored; we are still running
child killed by: SIGTERM
```

1. `signal.signal(SIGUSR1, on_usr1)` installed a **signal handler**: a Python function to run when that signal arrives. Sending `SIGUSR1` to ourselves ran it.
2. `signal.signal(SIGUSR2, signal.SIG_IGN)` set the disposition to **ignore** (`SIG_IGN`). Without that line, `SIGUSR2`'s **default action**, terminating the process, would have ended the snippet right there.
3. A forked child with the default disposition for `SIGTERM` was terminated by it, and the parent could see *which* signal killed it (more in [exit-status](exit-status.md)).

The second snippet is gate G1.2, the trick Kiln's event loop relies on:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/signals_wakeup.py
```

```text
bytes written to the wakeup socket: [10, 12, 10]
as signal names: ['SIGUSR1', 'SIGUSR2', 'SIGUSR1']
the Python handlers recorded: ['SIGUSR1', 'SIGUSR2', 'SIGUSR1']
```

After `signal.set_wakeup_fd(write_end.fileno())`, every time a signal arrives Python writes **one byte containing the signal number** into that socket. An event loop that is asleep in `select()` waiting on the *other* end wakes up, because data arrived. That is the **wakeup fd**, also called the self-pipe trick.

**Process groups and Ctrl-C.** Processes started from one shell command line form a **process group** (the `PGID` column). Here bash started `sleep` and `ps`, and all three share group 191:

```text
    PID    PPID    PGID COMMAND
    191     190     191 bash
    555     191     191 sleep
    556     191     191 ps
```

When you press Ctrl-C, the terminal sends `SIGINT` to **every process in the foreground group**. For `kiln serve` that means the master *and all its workers* (forked children stay in their parent's group). That's why Kiln's workers ignore `SIGINT` ([ADR-024](../decisions.md#adr-024)) and leave the decision to the master.

## How it really works

- **Delivery.** `kill(pid, sig)` (`man 2 kill`) asks the kernel to mark the signal *pending* on that process. The next time the process is about to return to user code, the kernel runs its disposition: default action, ignore, or jump to the registered C handler.
- **Python handlers run "between bytecodes".** CPython's C-level handler only sets a flag (plus the wakeup-fd byte). The main thread checks that flag between bytecode instructions and then calls your Python function. So the handler runs *in the main thread, at whatever line the main thread happened to be on*, possibly halfway through updating a dict.
  - Example: the master's loop is moving a job from the queue into a worker's in-flight set. If a `SIGHUP` handler called `reload()` right then, `reload()` would see the job in neither place, or in both.
  - Kiln's rule: **handlers only record the signal** (append its number to a list). The loop handles it later, at a known safe point ([spec/04 §3](../spec/04-supervisor.md#3-signals-the-master-handles)).
- **What survives `fork()` and `exec`.** A forked child inherits handlers, ignored signals and the wakeup fd setting. A freshly spawned program keeps "ignored" but resets handlers to default. Kiln's worker resets everything explicitly and calls `signal.set_wakeup_fd(-1)`. Otherwise its signals would write into the *master's* wakeup socket, which the child inherited.
- **`SIGKILL` and `SIGSTOP`** can't be caught, blocked or ignored. That's why an OOM kill leaves no Python traceback.
- **Standard signals don't queue.** Two `SIGCHLD`s arriving close together can look like one. That's why the reaping code loops "until nothing is left" instead of reaping once per signal ([zombies-and-reaping](zombies-and-reaping.md)).

## How Kiln uses it

- [`master/signals.py` → `SignalPipe`](../../src/kiln/master/signals.py) creates the socketpair, calls `set_wakeup_fd`, installs handlers that only append to `pending`, and has `drain()` for the loop.
- [`master/arbiter.py`](../../src/kiln/master/arbiter.py) registers the read end in its selector and maps each signal to an action, per the table above.
- [`worker/main.py` → `run_worker`](../../src/kiln/worker/main.py) ignores `SIGINT`, turns `SIGTERM` into "retire after the current job", and clears the inherited wakeup fd.

## Common misconceptions

- ❌ **Myth:** "A signal handler runs in its own thread, so it can't disturb my code."
  ✅ **Reality:** in Python it runs in the *main* thread, between two bytecodes of whatever the main thread was doing. That's why it must do almost nothing.
- ❌ **Myth:** "`kill` means kill."
  ✅ **Reality:** `kill` *sends a signal*. `kill -HUP` asks for a reload and `kill -USR1` asks for a status dump. Only some signals' default action is to terminate.
- ❌ **Myth:** "If a process got SIGKILL, someone typed `kill -9`."
  ✅ **Reality:** the kernel's OOM killer also sends `SIGKILL`. That's why Kiln says "suspected OOM" when it sees a SIGKILL it didn't send itself ([spec/04 §4.1](../spec/04-supervisor.md#41-death-classification-the-k1-oom-detection-requirement)).
- ❌ **Myth:** "Ctrl-C only reaches the program I started."
  ✅ **Reality:** it reaches the whole foreground process group, children included.

## Check yourself

1. What would go wrong if the master's `SIGHUP` handler called `supervisor.reload()` directly?
   <details><summary>Answer</summary>

   The handler runs in the main thread at an arbitrary point, possibly in the middle of the loop changing the job table or the worker table. `reload()` would see and modify half-updated state. Recording the signal and handling it at the top of the next loop pass avoids that.

   </details>
2. Predict: a script installs no handlers and sends itself `SIGUSR2`. What happens?
   <details><summary>Answer</summary>

   It dies. The default action for `SIGUSR2` is "terminate", and its parent sees "killed by SIGUSR2" (exit code 140 in a shell, which is 128 + 12).

   </details>
3. Why do Kiln's workers ignore `SIGINT`?
   <details><summary>Answer</summary>

   Ctrl-C sends `SIGINT` to the whole foreground process group, workers included. If workers died on it, a single Ctrl-C would kill them mid-job before the master could do a graceful shutdown. Ignoring it leaves the decision to the master.

   </details>
4. What does the wakeup fd give an event loop that a plain handler can't?
   <details><summary>Answer</summary>

   A way to *wake up*. A loop asleep in `select()` only wakes when one of its sockets has data. The wakeup fd turns "a signal arrived" into "a byte arrived on a socket", so the loop wakes and handles the signal promptly and safely.

   </details>

## Go deeper

- `man 7 signal`: every signal, its number and default action.
- [Python docs: `signal`](https://docs.python.org/3/library/signal.html), especially "Execution of Python signal handlers" and `set_wakeup_fd`.
- gunicorn `arbiter.py`: `init_signals`, `signal` and `wakeup`, the same pattern (K1 lab reference comparison).
- D. J. Bernstein's "self-pipe trick" note, the origin of the idea.
