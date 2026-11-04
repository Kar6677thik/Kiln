# Zombies and Reaping: Cleaning Up After Dead Children

> **Milestone:** K1 · **Read before:** [exit-status](exit-status.md), [signals](signals.md) · **Reading time:** ~10 min
> **Terms introduced:** **zombie**, **reaping**, **WNOHANG**, **SIGCHLD**

## One-sentence summary

When a child process dies, the kernel keeps a tiny record of it, a **zombie**, until the parent collects its exit status with `waitpid()`. Collecting it is called **reaping**.

## Why you should care

Kiln's master is the parent of every worker. If it never reaps, dead workers pile up as zombies: each one holds a pid and a slot in the process table, and the master never learns *how* the worker died. Reaping is where the supervisor's knowledge comes from. Every respawn, every "worker lost" job, every OOM report starts with a `waitpid()` call ([spec/04 §4](../spec/04-supervisor.md#4-reaping-and-classifying-deaths)).

## The mental model

**Analogy:** a parcel locker. When a child dies, the kernel puts its "how I died" note in a locker with the child's name on it and keeps the locker reserved until the parent opens it. Opening the locker is reaping; the locker then becomes free for someone else.

**Where the analogy fails:** a locker holds a parcel indefinitely without harm. Zombies use real, limited resources (pids, process-table entries), and they show up in `ps` with state `Z`, confusing anyone who thinks the program is still running.

```
  child running ──exits──▶ ZOMBIE (state Z) ──parent: waitpid()──▶ gone
                    │       • holds pid + status                   • status delivered
                    │       • uses no CPU/memory                   • pid can be reused
                    └──▶ kernel sends SIGCHLD to the parent
```

## See it for yourself

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/zombies-and-reaping_1.py
```

```text
after the child exited, its state is: Z
waitpid returned the child? True; exit code 7
after reaping, its state is: gone (no /proc entry)
waitpid(-1, WNOHANG) while a child is still running: (0, 0)
waitpid with no children left: ChildProcessError (No child processes)
```

1. The child called `os._exit(7)`, and the parent waited 0.2 s *without* reaping. `/proc/<pid>/stat` shows state **`Z`**: the child is dead but not gone.
2. `waitpid(pid, WNOHANG)` returned the child and its status (exit code 7). Immediately after, `/proc/<pid>` no longer exists. The zombie is reaped.
3. With **`WNOHANG`** ("no hang") and a child that is *still running*, `waitpid` doesn't wait. It returns `(0, 0)`, meaning "you have children, but none has exited".
4. With no children left at all, `waitpid` raises `ChildProcessError` (the C error `ECHILD`).

Those last three results are exactly the three ways Kiln's reap loop can end.

## How it really works

- `waitpid(-1, WNOHANG)` means "any child, and don't block". The master must never block ([ADR-006](../decisions.md#adr-006)), so it always passes `WNOHANG`.
- When a child exits, the kernel sends the parent **`SIGCHLD`**. Its default action is "ignore", so you have to ask for it, and Kiln's master does through its wakeup fd ([signals](signals.md)).
- Signals don't queue: three workers dying at once may deliver one `SIGCHLD`. So reaping is a loop that runs **until nothing is left**:

  ```python
  while True:
      try:
          pid, status = os.waitpid(-1, os.WNOHANG)
      except ChildProcessError:   # no children at all
          break
      if pid == 0:                # children exist, none has exited
          break
      handle_exit(pid, status)
  ```

  Kiln also runs this loop once a second as a safety net, in case a `SIGCHLD` was ever missed.
- If the *parent* dies first, its children become orphans. Linux re-parents them to pid 1 (systemd here), which reaps them. The original parent can't learn their exit status any more. That matters in K2 (forkserver).
- A process can only reap its **own** children. That's why, in K2's forkserver mode, the forkserver has to reap workers and report their statuses to the master.

## How Kiln uses it

- [`master/supervisor.py` → `on_exit`](../../src/kiln/master/supervisor.py) receives each `(pid, status)`. It classifies the death with `classify_exit` ([exit-status](exit-status.md)), resolves the worker's in-flight jobs, and maybe schedules a respawn.
- [`master/arbiter.py`](../../src/kiln/master/arbiter.py) runs the reap loop after every `SIGCHLD` and on a 1 s `reap_poll` timer.

## Common misconceptions

- ❌ **Myth:** "A zombie is a process that's stuck and using CPU."
  ✅ **Reality:** a zombie is already dead. It uses no CPU and no memory, only a process-table entry and its pid, waiting to be reaped.
- ❌ **Myth:** "You can kill a zombie with `kill -9`."
  ✅ **Reality:** it's already dead, so signals do nothing. Only its parent reaping it (or the parent dying, so pid 1 adopts and reaps it) removes it.
- ❌ **Myth:** "One `SIGCHLD` means one dead child."
  ✅ **Reality:** several deaths can merge into one signal. Always reap in a loop until `waitpid` reports nothing.

## Check yourself

1. What is a zombie process? What happens if the master never calls `waitpid`?
   <details><summary>Answer</summary>

   A zombie is a dead child whose exit status hasn't been collected. If the master never reaps, zombies accumulate (each holding a pid), and the master never learns that or how its workers died, so it can't respawn them or resolve their jobs.

   </details>
2. Predict: the master gets one `SIGCHLD` but two workers died. What does the reap loop return on its three iterations?
   <details><summary>Answer</summary>

   The first two iterations return `(pid, status)` for each dead worker. The third returns `(0, 0)` if other workers are still alive, or raises `ChildProcessError` if none are left, and the loop stops.

   </details>
3. Why does Kiln pass `WNOHANG`?
   <details><summary>Answer</summary>

   Without it, `waitpid` blocks until some child exits, which would freeze the single-threaded master and every client with it. `WNOHANG` makes it return immediately.

   </details>

## Go deeper

- `man 2 waitpid` and `man 7 signal` (the `SIGCHLD` entry).
- [Python docs: `os.waitpid`](https://docs.python.org/3/library/os.html#os.waitpid).
- gunicorn `arbiter.py` → `reap_workers`: the same loop.
