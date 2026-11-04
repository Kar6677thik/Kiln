# cgroups and the OOM Killer

> **Milestone:** K1 · **Read before:** [what-is-a-process](what-is-a-process.md), [memory-metrics](memory-metrics.md) · **Reading time:** ~15 min
> **Terms introduced:** **cgroup**, **memory.max**, **memory.events**, **OOM killer**, **oom_kill counter**, **oom_score_adj**, **systemd scope**, **swap**

## One-sentence summary

A **cgroup** puts a group of processes under a shared resource limit. When the group goes over its memory limit, the kernel's **OOM killer** picks a process in it and kills it with `SIGKILL`, with no warning and no chance to clean up.

## Why you should care

This is the Back Market incident (research [§2.7](../research/python-mastery-research.md#27-celery-and-pre-fork-workers-memory-multiplied-by-concurrency-secondary-sources)). Celery workers ran in containers with memory limits, and a container *is* a cgroup. When the workers together grew past the limit, the kernel killed one. The only symptom anyone saw was "worker lost". Kubernetes didn't even report an OOM event.

Kiln's success criterion SC-3 is to do better: detect the kill, say *it was probably the OOM killer*, and **confirm** it from the kernel's own counter ([spec/04 §4.2](../spec/04-supervisor.md#42-oom-evidence)).

## The mental model

**Analogy:** a shared office budget. The team (the cgroup) has a monthly limit (`memory.max`). When the team overspends, accounting (the kernel) doesn't send a warning email. It fires one team member on the spot, usually the biggest spender. The team's file (`memory.events`) gets a tally mark: "`oom_kill` +1".

**Where the analogy fails:** a fired employee can at least say goodbye. `SIGKILL` can't be caught, blocked or handled: no `finally`, no `atexit`, no last log line. The process simply stops existing. Only its *parent* learns how it ended, through `waitpid` ([exit-status](exit-status.md)).

```
 /sys/fs/cgroup/                              ← one tree (cgroup v2), a folder per group
   user.slice/user-1000.slice/user@1000.service/
     app.slice/                               memory.events: oom_kill counts THIS folder AND below
       run-r…scope/          memory.max = 64M     ← our experiment's scope
         └── python  (asks for 200 MiB) ──▶ over the limit ──▶ OOM killer ──▶ SIGKILL
                                                   parent sees: exit status 137 (128 + 9)
```

## See it for yourself

**1. Which cgroup am I in?**

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/cgroups-and-oom-killer_1.py
```

```text
/proc/self/cgroup: 0::/init.scope
memory.events path: /sys/fs/cgroup/init.scope/memory.events
low 0
high 0
max 0
oom 0
oom_kill 0
oom_group_kill 0
```

- On cgroup v2 there is one line, `0::/path`. Commands started through `wsl.exe` land in `/init.scope`. A terminal you open yourself is usually under `/user.slice/...`.
- `memory.events` is the group's tally of memory events:
  - `max`: times the limit was hit;
  - `oom`: times the group ran out;
  - `oom_kill`: processes the OOM killer killed.

**2. A real OOM kill, safely inside a 64 MiB scope:**

```bash
(wsl) $ bash docs/concepts/snippets/cgroups-and-oom-killer_2.sh
```

```text
child cgroup: /user.slice/user-1000.slice/user@1000.service/app.slice/run-rce86201732054ea0a9d6252111074f74.scope
exit status: 137   (128 + 9 = 137: killed by signal 9, SIGKILL)
app.slice oom_kill: 2 -> 3
```

(Bash also prints a "Killed" line echoing the command, trimmed here.)

- `systemd-run --user --scope -p MemoryMax=64M -p MemorySwapMax=0` created a new cgroup (a **systemd scope**) with a 64 MiB limit and no **swap**, then ran Python in it.
- Python asked for 200 MiB. Its line "allocated?!" never printed: the kernel killed it before `bytearray` returned.
- **Exit status 137** is the shell's way of saying "killed by signal 9". That's all the parent ever sees.
- `oom_kill` went **2 → 3**. That number is the evidence that turns "a SIGKILL happened" into "the OOM killer did it". (It started at 2 because the same experiment had been run twice before.)
- We read `app.slice`, the scope's *parent*. A scope's own folder disappears the moment its last process exits, and `memory.events` counts events in a group *and all its children*.

## How it really works

- **cgroup v2** is one tree of folders under `/sys/fs/cgroup`. Each folder is a group, and writing a pid into its `cgroup.procs` moves that process there. Children of a process start in their parent's group, so a Kiln master started inside a scope keeps all its workers in that scope too.
- **`memory.max`** is the hard limit. When the group's memory use reaches it, the kernel first tries to reclaim memory (dropping file caches, swapping). If that fails, it invokes the **OOM killer** for that group only. The rest of WSL is unaffected, which is why the lab uses a cgroup ([ADR-023](../decisions.md#adr-023)).
- **Swap matters.** If swapping is allowed, the kernel may swap for minutes before killing anything. `MemorySwapMax=0` (`memory.swap.max = 0`) makes the outcome fast and predictable.
- **Who gets killed?** The kernel scores each process, mostly by memory size, and adds that process's **`oom_score_adj`** (−1000 to +1000, in `/proc/<pid>/oom_score_adj`). The highest score dies. Any process may *raise* its own score without privileges, and Kiln workers raise theirs to 500 so the kernel kills a worker rather than the master ([ADR-029](../decisions.md#adr-029)). The supervisor must survive to report the death.
- **Why "suspected" and "confirmed" are different:** a SIGKILL can come from anyone, such as `kill -9`, a test, or the worker itself (`labs.crash how=sigkill_self`). Only an increment of the `oom_kill` counter proves the kernel's OOM killer did it.

## How Kiln uses it

- [`master/supervisor.py` → `classify_exit`](../../src/kiln/master/supervisor.py) classifies a SIGKILL the master didn't send itself as `killed(SIGKILL, suspected_oom=true)` (spec/04 §4.1).
- [`master/supervisor.py` → `read_oom_kill_count`](../../src/kiln/master/supervisor.py) reads `oom_kill` from the master's own cgroup's `memory.events`, found via `/proc/self/cgroup`. It reads at startup and again after each unexplained SIGKILL. If the counter rose, the death is logged with `oom_confirmed=true`. If the file can't be read, it reports `oom_evidence="unavailable"` and never crashes. In the K1 lab the master runs *inside* the scope, so the scope's file exists for as long as the master lives.
- Workers write 500 to `/proc/self/oom_score_adj` at boot (`worker/main.py`).
- `kiln doctor` D12/D13 check cgroup v2 and that `systemd-run --user --scope` works.

## Common misconceptions

- ❌ **Myth:** "a program that runs out of memory gets a `MemoryError` it can handle."
  ✅ **Reality:** sometimes Python raises `MemoryError` (when one allocation is refused outright). Under a cgroup limit, the usual outcome is a `SIGKILL` from the OOM killer, which nothing can catch.
- ❌ **Myth:** "a SIGKILL'd worker means out-of-memory."
  ✅ **Reality:** it *suggests* OOM. Anything with permission can send SIGKILL. Kiln calls it "suspected" until the `oom_kill` counter confirms it.
- ❌ **Myth:** "the OOM killer kills the process that made the last allocation."
  ✅ **Reality:** it kills the process with the highest *score* (mostly size, adjusted by `oom_score_adj`). That might be a big neighbour, or the master, unless you steer it.

## Check yourself

1. A worker exits with status 137 and the master never sent it a signal. What two explanations are possible, and how does Kiln tell them apart?
   <details><summary>Answer</summary>

   Either the kernel's OOM killer, or someone else sent `SIGKILL` (for example `kill -9`). Kiln checks the `oom_kill` counter in its cgroup's `memory.events`. If it increased, the OOM killer is confirmed. Otherwise the cause stays "suspected" only.

   </details>
2. Why does the lab set `MemorySwapMax=0`?
   <details><summary>Answer</summary>

   With swap allowed, the kernel may move memory to disk for a long time before killing anything, so the experiment becomes slow and unpredictable. With no swap, hitting `memory.max` leads to an OOM kill quickly.

   </details>
3. Predict: Kiln's master runs inside a 512 MiB scope with 2 workers, and one worker allocates 700 MiB. Which process should die, and why?
   <details><summary>Answer</summary>

   The worker. It's the biggest process, and it also raised its `oom_score_adj` to 500, so it scores far higher than the master. The master survives, reaps it, and logs the classified death.

   </details>
4. Why does the docs snippet read `app.slice/memory.events` instead of the scope's own file?
   <details><summary>Answer</summary>

   A scope's folder is removed when its last process exits, so after the kill there's nothing left to read. `memory.events` in a parent group counts events of all its children, so the parent's counter shows the increment.

   </details>

## Go deeper

- Linux kernel docs, [Control Group v2](https://docs.kernel.org/admin-guide/cgroup-v2.html): the "Memory" section explains `memory.max`, `memory.events` and `memory.swap.max`.
- `man 5 proc`: `/proc/<pid>/oom_score` and `oom_score_adj`.
- `man 1 systemd-run`: `--scope` and resource properties like `MemoryMax=`.
- [spec/04 §4](../spec/04-supervisor.md#4-reaping-and-classifying-deaths): how Kiln classifies deaths.
