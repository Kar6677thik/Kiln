# Reparenting, Orphans and pidfds

> **Milestone:** K2 · **Read before:** [zombies-and-reaping](zombies-and-reaping.md), [start-methods](start-methods.md) · **Reading time:** ~12 min
> **Terms introduced:** **orphan**, **reparenting**, **subreaper**, **pid reuse**, **pidfd**

## One-sentence summary

When a parent dies, its children become **orphans** and are **reparented** to an ancestor (init, or a designated **subreaper**). Only a process's *parent* can `waitpid` it, so anyone else who wants to know when it exits uses a **pidfd**: an fd that refers to exactly that process and becomes readable when it dies.

## Why you should care

Under the forkserver start method, Kiln's workers are children of the *forkserver*, not of the master. If the forkserver crashes, those workers keep running, but now they're orphans. The master was never their parent, so it can't `waitpid` them. Polling with `os.kill(pid, 0)` ("does this pid exist?") is a trap: after the orphan dies and is reaped by its new parent, the kernel may give the **same pid** to a brand-new, unrelated process (**pid reuse**), and the master would think its worker is still alive. spec/04 §6 rule 4 therefore says: open a pidfd for every forkserver worker *as soon as it's spawned*.

The forkserver also has the reverse duty. If the *master* dies, the forkserver must kill its children, or they'd run forever with nobody to report to (K2 spec §5.4, EOF rule).

## The mental model

**Analogy:** a pid is a phone number. When someone moves out, their number is eventually given to a stranger, so calling the number tells you nothing reliable. A pidfd is a direct line installed to that one person. It rings when they leave, and it can never be reconnected to anyone else.

**Where the analogy fails:** the pidfd doesn't let you collect the exit status. Only the real parent can do that with `waitpid`. A pidfd only tells you *that* it exited.

```
 before:  master ──▶ forkserver ──▶ worker W (pid 4548)
 forkserver dies:
          master        worker W ── reparented to ──▶ subreaper / init (here: WSL's "Relay")
            │ waitpid(4548)?  ✗ ChildProcessError (not our child)
            │ kill(4548, 0)?  ✗ unreliable after pid reuse
            └ pidfd = pidfd_open(4548) at spawn time ─▶ selector ─▶ readable when W exits ✓
```

## See it for yourself

[`snippets/pidfd_orphan.py`](snippets/pidfd_orphan.py) (gate G2.4) forks a middle process that forks a grandchild and then exits immediately. The grandchild becomes an orphan:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/pidfd_orphan.py
```

```text
grandchild pid 4548 is an orphan now
its new parent is pid 4544 (Relay(4545)), not us (pid 4546)
waitpid on it fails: ChildProcessError (No child processes)
pidfd readable: True after about 0.5 s
```

- **Line 2:** the orphan's new parent isn't us. Under WSL it's the `Relay` process (WSL's per-session init helper, acting as a subreaper). On a normal Linux system it would be pid 1 or a subreaper such as `systemd --user`.
- **Line 3:** `waitpid` refuses: it isn't our child (ECHILD).
- **Line 4:** the pidfd we opened became readable about 0.5 s later, exactly when the grandchild exited.

## How it really works

- **Reparenting.** When a process exits, the kernel walks its children and re-parents each one to the nearest ancestor marked as a **child subreaper** (`prctl(PR_SET_CHILD_SUBREAPER)`), or to pid 1 if there isn't one. The new parent receives SIGCHLD when the orphan later dies, and it reaps it.
- **Who may `waitpid`.** Only the current parent. To everyone else, `waitpid(pid)` returns ECHILD, which Python raises as `ChildProcessError`.
- **pid reuse.** pids are recycled (up to `/proc/sys/kernel/pid_max`). Once a process is reaped, its number can be handed to the next new process. Any "check by pid later" scheme has a race unless it holds a stable reference.
- **pidfd (Linux 5.3+, Python 3.9+).** `os.pidfd_open(pid)` returns an fd bound to *that* process. If you open it while the process is alive, it can never refer to a different process. The fd polls as readable once the process has exited, so it fits a `selectors` loop like any socket. **Open it early**: opening a pidfd *after* the process might already have died and its pid been reused would attach you to the wrong process.

## How Kiln uses it

- [`src/kiln/launch/forkserver.py`](../../src/kiln/launch/forkserver.py): on each `spawned{worker_id, pid}` message, the master calls `os.pidfd_open(pid)` immediately and keeps it on the WorkerHandle.
- If the forkserver dies, the master registers those pidfds in its selector. A readable pidfd → the worker is classified `unknown_exit`, and its jobs become `worker_lost` (spec/04 §6 rule 4).
- [`src/kiln/launch/forkserver_main.py`](../../src/kiln/launch/forkserver_main.py): when the control socket reaches EOF (the master died), it sends SIGTERM to all children, waits up to 5 s, then SIGKILLs the rest, so no worker outlives the master.
- Code tag: `CONCEPT(reparenting-and-orphans)`.

## Common misconceptions

- ❌ **Myth:** "When a parent dies, its children die too."
  ✅ **Reality:** they keep running, reparented to init or a subreaper. Killing them is someone's explicit job (Kiln's forkserver does it on EOF).
- ❌ **Myth:** "`os.kill(pid, 0)` tells me whether my process is alive."
  ✅ **Reality:** it tells you whether *some* process with that number exists. After pid reuse, that may be a stranger.
- ❌ **Myth:** "With a pidfd I can get the exit code of any process."
  ✅ **Reality:** only the parent can reap and read the exit status. A pidfd tells a non-parent *that* the process exited, not how.

## Check yourself

1. Why can't Kiln's master `waitpid` a worker created by the forkserver?
   <details><summary>Answer</summary>

   The forkserver called `fork()`, so it's the worker's parent, and only the parent may wait for a child. The master gets exit statuses from the forkserver's `child_exit` messages instead.

   </details>
2. Why must the pidfd be opened when the worker is spawned, not when the forkserver dies?
   <details><summary>Answer</summary>

   By the time the forkserver dies, a worker might already have exited, been reaped, and had its pid reused by an unrelated process. Opening a pidfd then could attach to the wrong process. Opened at spawn time, while the worker is certainly alive, it can only ever refer to that worker.

   </details>
3. Predict: in the snippet, if the grandchild lived 2 s instead of 0.5 s, what would line 4 print?
   <details><summary>Answer</summary>

   `pidfd readable: True after about 2.0 s`. `select` waits (up to its 5 s timeout) until the pidfd becomes readable, which happens when the grandchild exits.

   </details>

## Go deeper

- `man 2 pidfd_open`, `man 2 prctl` (`PR_SET_CHILD_SUBREAPER`).
- [Python docs: `os.pidfd_open`](https://docs.python.org/3.15/library/os.html#os.pidfd_open).
- [LWN: "Toward race-free process signaling"](https://lwn.net/Articles/773459/): why pidfds were added.
