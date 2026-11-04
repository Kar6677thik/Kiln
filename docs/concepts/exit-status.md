# Exit Status: How a Parent Learns How Its Child Ended

> **Milestone:** K1 · **Read before:** [what-is-a-process](what-is-a-process.md), [signals](signals.md) · **Reading time:** ~10 min
> **Terms introduced:** **wait status**, **exit code**, **terminated by signal**, **core dump**, **128+N convention**

## One-sentence summary

When a child process ends, its parent receives one integer, the **wait status**. It says *either* "exited normally with exit code N" *or* "was terminated by signal S", and you need small helper functions to tell which.

## Why you should care

"How did the worker die?" is the most important question a supervisor answers. Back Market's Celery saw only "worker lost" (research §2.7). The difference between "the task called `sys.exit(1)`", "the code segfaulted" and "the kernel OOM-killed it" is all inside this one integer. Kiln's death classification ([spec/04 §4.1](../spec/04-supervisor.md#41-death-classification-the-k1-oom-detection-requirement)) is a careful reading of it.

## The mental model

**Analogy:** a sealed envelope from a hospital. Inside is *either* a discharge note with a number ("left on their own, condition 0 = fine, 3 = not great") *or* a cause of death ("signal 9"). You have to check which kind of note it is before you read the number. Reading a cause of death as a discharge code gives nonsense.

**Where the analogy fails:** the "envelope" is literally one integer with the two cases packed into different bits, so reading the wrong field doesn't fail: it gives a plausible-looking wrong number.

```
 wait status (16 low bits on Linux)
 ┌──────────────┬────────────────────────────┐
 │  bits 8–15   │  bits 0–7                  │
 ├──────────────┼────────────────────────────┤
 │ exit code    │ 0                          │  → exited normally:  WIFEXITED, WEXITSTATUS
 │ 0            │ signal number (+0x80 core) │  → killed by signal: WIFSIGNALED, WTERMSIG, WCOREDUMP
 └──────────────┴────────────────────────────┘
 examples: exit 3 → 0x0300     SIGKILL → 0x0009
```

## See it for yourself

Gate G1.10:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/exit-status_1.py
```

```text
exit 0   raw status=0x0000  exited, code 0        waitstatus_to_exitcode=0
exit 3   raw status=0x0300  exited, code 3        waitstatus_to_exitcode=3
SIGKILL  raw status=0x0009  killed by SIGKILL     waitstatus_to_exitcode=-9
```

- `os._exit(3)` produced the raw status `0x0300`: the code 3 sits in the *high* byte.
- A child killed by `SIGKILL` produced `0x0009`: the signal number 9 in the *low* byte, and no exit code at all.
- `os.waitstatus_to_exitcode` (Python 3.9+) squashes both cases into one number: the exit code, or **minus** the signal number. It's handy for display, but Kiln uses the explicit helpers so it can also see the core-dump flag.

Shells use their own convention, the **128+N convention**: a command killed by signal N reports `$?` = 128 + N. Real output (run from a script, so the WSL `$?` gotcha from the [K0 setup log](../tours/K0.md#appendix-setup-log) doesn't apply):

```text
exit code after kill -9: 137
exit code after exit 3: 3
```

137 = 128 + 9 means "killed by SIGKILL". You'll see 137 in Docker and Kubernetes, where it very often means an OOM kill.

## How it really works

- The parent calls `waitpid(pid, options)` (`man 2 waitpid`) and gets back `(pid, status)`. Python's `os` module provides the decoding macros from C:
  - `os.WIFEXITED(status)`: did it exit normally? Then `os.WEXITSTATUS(status)` is the **exit code** (0–255).
  - `os.WIFSIGNALED(status)`: was it **terminated by a signal**? Then `os.WTERMSIG(status)` is the signal number, and `os.WCOREDUMP(status)` says whether the kernel wrote a **core dump**.
- An **exit code** is chosen by the program: `sys.exit(2)`, `os._exit(3)`, or the 0 Python uses for a normal end and 1 for an uncaught exception. Only the low 8 bits survive, so `sys.exit(256)` reports 0.
- A **core dump** is a file containing the dead process's memory, for debuggers. Signals such as `SIGSEGV` and `SIGABRT` ask for one, but it's only written if the limit allows it. On this machine `ulimit -c` prints **0**, so none are written and `WCOREDUMP` stays false.
- There's a third case, "stopped" (`WIFSTOPPED`), which Kiln doesn't ask for and never sees.

## How Kiln uses it

[`master/supervisor.py` → `classify_exit`](../../src/kiln/master/supervisor.py) decodes the status and combines it with what the master knows, such as "did *we* send that SIGKILL?" ([spec/04 §4.1](../spec/04-supervisor.md#41-death-classification-the-k1-oom-detection-requirement)):

| Status | Kiln's classification |
|---|---|
| exited 0 while RETIRING | `retired` |
| exited 3 while BOOTING | `boot_failed` (Kiln's own code for "the app failed to import") |
| exited n ≠ 0 | `crashed(exit_code=n)` |
| SIGKILL not sent by the master | `killed(SIGKILL, suspected_oom=true)` |
| another signal | `killed(signal=<name>, core_dumped=<bool>)` |

Kiln's own exit codes are listed in [spec/04 §5.6](../spec/04-supervisor.md#56-shutdown): 0 clean, 1 forced, 2 usage error, 3 worker boot error, 4 app failed to boot repeatedly.

## Common misconceptions

- ❌ **Myth:** "The status from `waitpid` *is* the exit code."
  ✅ **Reality:** it's an encoded integer. `exit 3` arrives as 768 (`0x0300`), so you must decode it.
- ❌ **Myth:** "Exit code 137 means the program returned 137."
  ✅ **Reality:** in a shell, 137 almost always means "killed by signal 9" (128 + 9). The program never got to choose a code.
- ❌ **Myth:** "A process killed by a signal has exit code 0 or 1."
  ✅ **Reality:** it has *no* exit code at all; `WIFEXITED` is false.

## Check yourself

1. A worker's status says "terminated by signal 9", and Kiln's `kill_sent` is `None`. What does Kiln log, and why only "suspected" OOM?
   <details><summary>Answer</summary>

   `killed(SIGKILL, suspected_oom=true)`. Kiln didn't send that SIGKILL, and the most common sender is the kernel's OOM killer, but a human running `kill -9` looks identical. Only the cgroup's `oom_kill` counter going up *confirms* an OOM ([spec/04 §4.2](../spec/04-supervisor.md#42-oom-evidence)).

   </details>
2. Predict the raw status for a child that calls `os._exit(5)`.
   <details><summary>Answer</summary>

   `0x0500` (1280): the exit code goes in the high byte, and the low byte is 0.

   </details>
3. What does `os.waitstatus_to_exitcode` return for a child killed by `SIGTERM` (15)?
   <details><summary>Answer</summary>

   `-15`: negative numbers mean "killed by that signal".

   </details>

## Go deeper

- `man 2 waitpid`, section "wstatus".
- [Python docs: `os.waitstatus_to_exitcode`](https://docs.python.org/3/library/os.html#os.waitstatus_to_exitcode) and the `os.W*` functions.
- gunicorn `arbiter.py` → `reap_workers`: how gunicorn decodes the same integer (the K1 reference comparison).
