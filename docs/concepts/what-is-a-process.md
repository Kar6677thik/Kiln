# What Is a Process?

> **Milestone:** K0 · **Read before:** [terminal-and-wsl](terminal-and-wsl.md) · **Reading time:** ~15 min
> **Terms introduced:** **program**, **process**, **pid**, **parent process**, **child process**, **memory isolation**, **/proc**, **kernel**

## One-sentence summary

A **program** is a file of instructions sitting on disk. A **process** is that program *running*, with its own private memory, its own id number (**pid**) and a parent that started it.

## Why you should care

Kiln is a team of processes: one **master** and several **workers** ([spec/01 §2](../spec/01-architecture.md#2-processes-and-their-roles)). Almost every hard lesson in this project is really about processes:

- **Why one crash doesn't kill everything.** A worker that dies takes only its own memory with it.
- **How the master learns a worker died.** A parent can collect its child's **exit code**. The Celery "worker lost" incident ([research §2.7](../research/python-mastery-research.md#27-celery-and-pre-fork-workers-memory-multiplied-by-concurrency-secondary-sources)) is about exactly this.
- **Why sharing memory between workers is hard and interesting.** Instagram's copy-on-write story ([research §2.1](../research/python-mastery-research.md#21-instagram-the-gc-breaks-copy-on-write-memory-sharing)) is about it.

If "process" is a fuzzy word, all of that stays fuzzy. This primer makes it concrete, and K1 then goes much deeper.

## The mental model

**Analogy:** a program is a **recipe** printed in a cookbook. A process is **someone actually cooking** that recipe in their own kitchen, with their own counter space (memory), a ticket number (pid), and a note of who asked them to cook (the parent). Ten cooks can follow the same recipe at once in ten kitchens, and nothing one cook spills lands in another's kitchen.

**Where the analogy fails:** real kitchens are separate buildings. Processes all run on one machine, sharing the CPUs and the RAM chips, and the operating system **kernel** constantly switches the CPUs between them. They can also *deliberately* share some memory (that's the copy-on-write story in K3). The kernel is the strict building manager who enforces the walls.

```
   disk:  /home/Megatron/.local/bin/python3.15        ← the PROGRAM (a file; it does nothing by itself)
                 │                │
         started │                │ started again
                 ▼                ▼
   ┌──────────────────────┐   ┌──────────────────────┐
   │ process  pid 1983    │   │ process  pid 1984    │   ← two PROCESSES of the same program
   │ parent:  pid 1982    │   │ parent:  pid 1983    │   ← 1983 is 1984's parent
   │ memory:  private     │   │ memory:  private     │   ← neither can read the other's variables
   │ open files: 0,1,2,…  │   │ open files: 0,1,2,…  │
   │ exit code: (running) │   │ exit code: 3         │   ← reported to the parent when it ends
   └──────────┬───────────┘   └──────────────────────┘
              │
   ┌──────────▼──────────────────────────────────────────────────────────┐
   │ KERNEL: schedules CPUs, enforces memory walls, keeps the process     │
   │ table, and exposes it as the /proc folder                            │
   └──────────────────────────────────────────────────────────────────────┘
```

## See it for yourself

The snippet [`snippets/what-is-a-process_1.py`](snippets/what-is-a-process_1.py) makes a process look at itself, then start a child process:

```bash
(wsl) $ python3.15 docs/concepts/snippets/what-is-a-process_1.py
```

```text
my pid (process id)      : 2003
my parent's pid          : 2002
  /proc/self/status -> Name:	python3.15
  /proc/self/status -> Pid:	2003
  /proc/self/status -> PPid:	2002
  /proc/self/status -> VmRSS:	   13696 kB
  /proc/self/status -> Threads:	1
  child: pid 2004 parent 2003
child finished with exit code: 3
```

Line by line (your numbers will differ: pids are handed out as processes start):
- `my pid ... 2003`: the kernel's id for this running Python.
- `my parent's pid ... 2002`: the process that started it. Here that's the shell that ran the command.
- The `/proc/self/status` lines are the same facts, read from a file the kernel generates on demand. `VmRSS` is how much RAM the process is using right now (about 13 MB for a small Python). `Threads: 1` means one thread of execution.
- `child: pid 2004 parent 2003`: the snippet started a *second* Python process. Its parent is 2003, our snippet.
- `child finished with exit code: 3`: the child ended by calling `sys.exit(3)`, and the parent was told the number 3.

> **A detail worth noticing:** the snippet calls `sys.stdout.flush()` just before starting the child. Without that, the parent's earlier lines can appear *after* the child's line. The parent keeps its output in a memory buffer, while the child writes straight out. Two processes, two separate buffers, which is memory isolation in action. The first run of this snippet showed the out-of-order version.

### Looking at processes from the shell

`ps` lists processes. Real output (trimmed) from a WSL shell:

```text
$ ps -o pid,ppid,stat,comm -u "$USER"
  PID  PPID STAT COMMAND
  216     1 Ss   systemd
  244   205 Ss+  zsh
 2070  2069 Ss+  bash
 2080  2070 R+   ps
```

`ps` itself shows up as a process (pid 2080), and its parent is the bash shell (2070) that started it.

Exit codes for a process that was *killed* look different. Real output: start a `sleep`, kill it, and ask how it ended:

```text
$ echo "this shell's pid: $$"; sleep 30 & SLEEP_PID=$!; ps -o pid,ppid,comm -p $$,$SLEEP_PID
this shell's pid: 2070
  PID  PPID COMMAND
 2070  2069 bash
 2082  2070 sleep
$ kill $SLEEP_PID; wait $SLEEP_PID 2>/dev/null; echo "sleep was killed; wait reported exit code $?"
sleep was killed; wait reported exit code 143
```

`143` = 128 + 15, and 15 is the number of the signal `kill` sends by default (SIGTERM). The shell's convention "128 + signal number" means "this process didn't exit by itself; it was killed by a signal". K1 (see [spec/04 §4.1](../spec/04-supervisor.md#41-death-classification-the-k1-oom-detection-requirement) and its `exit-status` primer) shows how Kiln's master tells "exited with code 3" apart from "killed by signal 9". That difference is how it detects out-of-memory kills.

## How it really works

**Creating a process.** On Linux, every process is created by an existing one, its **parent**, using the `fork` system call. Usually the child then loads a different program with `exec`. `subprocess.run` in the snippet does both for you. Following parents upward always ends at **pid 1**, the first process the kernel starts. In this WSL it's `systemd` (real output of `cat /proc/1/comm`: `systemd`).

**What each process has of its own:**

| Thing | Meaning |
|---|---|
| pid | its id, unique among *running* processes (ids are reused later) |
| parent pid (ppid) | who started it |
| memory | a private address space. A variable in one process is invisible to every other process (**memory isolation**) |
| open files | its own table of file descriptors (the [next primer](files-and-file-descriptors.md)) |
| exit code | a number 0–255 it reports when it ends. By convention 0 is success |

**Memory isolation** is enforced by the hardware (the CPU's memory-management unit) under the kernel's control. Each process sees its *own* map of addresses. Address `0x7f…1000` in process A and the same address in process B usually refer to completely different pieces of RAM. That's why a bug that corrupts memory in one Kiln worker can't corrupt the master.

**`/proc`** is a pretend folder: nothing in it is stored on disk. When you read `/proc/2003/status`, the kernel generates the text right then, from its own records about process 2003. `/proc/self` always means "the process that is reading this". A few useful entries (real `ls /proc/self`, partial):

```text
cgroup cmdline comm cwd environ exe fd limits maps mounts oom_score_adj smaps_rollup stat status
```

`status` has the human-readable summary. `fd` lists open files. `smaps_rollup` has detailed memory numbers (K3 uses them). `oom_score_adj` influences which process the kernel kills when memory runs out (K1).

**Ending.** A process ends either by **exiting**, meaning it calls exit with a code, or by being **killed** by a signal. Its parent collects the result with the `wait`/`waitpid` system call. Until the parent does, the dead child lingers as a *zombie*. K1's `zombies-and-reaping` primer covers that.

## How Kiln uses it

- [`src/kiln/doctor.py`](../../src/kiln/doctor.py) reads several `/proc` files (for example `/proc/meminfo` for total RAM, `/proc/sys/kernel/yama/ptrace_scope` for whether one process may inspect another, `/proc/self/smaps_rollup`, and `/proc/1/comm` to see whether pid 1 is systemd).
- `kiln --version` is itself a process: `python -m kiln` starts Python, which runs the `kiln` package's `__main__.py` ([`src/kiln/__main__.py`](../../src/kiln/__main__.py)).
- From K1 on, the **master** process starts **worker** child processes, watches their exit codes and replaces them when they die ([spec/04](../spec/04-supervisor.md)).

## Common misconceptions

- ❌ **Myth:** a program and a process are the same thing.
  ✅ **Reality:** one program file can run as many processes at once. Kiln's four workers are four processes, often all running the same `python3.15` program.
- ❌ **Myth:** a pid identifies a process forever.
  ✅ **Reality:** pids are reused after a process ends and is reaped. That's why Kiln (from K2) uses *pidfds*, which can't be confused with a newer process that happens to get the same number.
- ❌ **Myth:** if one process crashes, everything in the program crashes.
  ✅ **Reality:** a crash ends only *that* process. Kiln is designed around this: a worker can die, and the master survives to report it and start a replacement.
- ❌ **Myth:** `/proc` files are log files written by programs.
  ✅ **Reality:** they're generated live by the kernel each time you read them. Nothing is stored.

## Check yourself

1. You start `python3.15` three times in three terminals. How many programs and how many processes are there?
   <details><summary>Answer</summary>

   One program (the `python3.15` file on disk) and three processes, each with its own pid and private memory.

   </details>
2. Predict: a child process sets a variable `x = 42`. Can its parent read `x` afterwards?
   <details><summary>Answer</summary>

   No. Each process has private memory. To get information back, the child must *send* it somehow: an exit code, output text, a file or a socket. Kiln workers send results to the master over a socket.

   </details>
3. A shell reports exit code 137 for a process. What probably happened?
   <details><summary>Answer</summary>

   137 = 128 + 9, and signal 9 is SIGKILL. The process was killed, possibly by the kernel's out-of-memory killer, which uses SIGKILL. Detecting this situation correctly is a K1 requirement.

   </details>
4. Why can `/proc/self/status` give different answers to different programs reading "the same file"?
   <details><summary>Answer</summary>

   `/proc/self` means "whichever process is reading", and the kernel generates the content on demand for that reader. `cat` reading it sees `cat`'s status, and Python reading it sees Python's.

   </details>

## Go deeper

- `man 5 proc`, the reference for every file under `/proc` (try `man 5 proc` in WSL).
- `man 2 fork` and `man 2 wait`: how processes are created and collected (K1 reads these properly).
- [Python docs: `subprocess`](https://docs.python.org/3/library/subprocess.html): starting child processes from Python.
- [Python docs: `os.getpid`](https://docs.python.org/3/library/os.html#os.getpid) and related process functions.
