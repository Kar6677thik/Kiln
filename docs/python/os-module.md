# The os module (processes, exit statuses, environment)

> **First used in:** see the [ledger](README.md) (milestone K1) · **Needs:** [what-is-a-process](../concepts/what-is-a-process.md), [signal-module](signal-module.md) · **Reading time:** ~9 min
> **Terms introduced:** **os.fork**, **os._exit**, **os.waitpid**, **wait status**, **environment variable**, **ChildProcessError**

## What it looks like

```python
import os

pid = os.fork()                       # now there are two processes
if pid == 0:
    os._exit(3)                       # child: leave immediately with exit code 3
done_pid, status = os.waitpid(pid, 0) # parent: wait for the child and collect its status
os.WEXITSTATUS(status)                # 3
```

## What it means

The `os` module gives Python direct access to the operating system. The parts Kiln uses:

| Function | What it does | Underlying syscall |
|---|---|---|
| `os.getpid()` / `os.getppid()` | my process id / my parent's | `getpid`, `getppid` |
| `os.environ` | the **environment variables**: text settings passed from parent to child, like `HOME` or `KILN_WORKER__MAX_JOBS`. It behaves like a dict: `os.environ.get("X", "default")` | — |
| `os.fork()` | copy the current process. It returns **twice**: `0` in the new child, and the child's pid in the parent | `fork` |
| `os._exit(code)` | end the process **right now**: no `finally` blocks, no `atexit` handlers, no buffer flushing | `_exit` |
| `os.waitpid(pid, options)` | wait for a child to end and collect its **wait status**. `pid=-1` means "any child"; `options=os.WNOHANG` means "don't wait, return `(0, 0)` if none has finished" | `wait4` |
| `os.kill(pid, sig)` | send a signal to a process | `kill` |

**Decoding a wait status.** The status is one integer that packs two different stories. These helper functions unpack it:

| Helper | Answers |
|---|---|
| `os.WIFEXITED(status)` | did it end normally (by exiting)? |
| `os.WEXITSTATUS(status)` | if so, with which exit code? |
| `os.WIFSIGNALED(status)` | was it killed by a signal? |
| `os.WTERMSIG(status)` | if so, which signal? |
| `os.waitstatus_to_exitcode(status)` | shortcut: the exit code, or **minus** the signal number (`-9` for SIGKILL) |

## Why people use it

These are the building blocks of every process supervisor: gunicorn, Celery's pool and Kiln's master all fork children, wait for them, decode how they died, and send them signals. `os._exit` exists for exactly the forked-child case: the child must not run the parent's cleanup code a second time.

## How Kiln uses it

- [`src/kiln/launch/fork.py`](../../src/kiln/launch/fork.py): `fork_worker()` calls `os.fork()`. The child runs the worker and always ends with `os._exit(code)` ([ADR-028](../decisions.md#adr-028)).
- [`src/kiln/master/supervisor.py`](../../src/kiln/master/supervisor.py): the reap loop calls `os.waitpid(-1, os.WNOHANG)` until no exited child remains, and `classify_exit` uses `WIFEXITED`, `WEXITSTATUS`, `WIFSIGNALED` and `WTERMSIG` to tell "exited with code 1" from "killed by SIGKILL" (spec/04 §4.1). `os.kill` sends SIGKILL to hung or timed-out workers.
- [`src/kiln/config.py`](../../src/kiln/config.py) reads `KILN_<SECTION>__<KEY>` overrides from `os.environ`.

## Gotchas

- **`os.fork()` returns twice.** Code after it runs in *both* processes. Always branch on `if pid == 0:` immediately.
- **A child that returns normally falls back into the parent's code.** If the child branch doesn't end with `os._exit`, it continues with the parent's next lines, and you have two copies of your program doing the parent's job.
- **`sys.exit()` in a forked child is wrong too.** It raises `SystemExit`, which runs the `finally` blocks and `atexit` handlers inherited from the parent.
- **Flush before forking.** Text still waiting in `print`'s buffer gets copied into the child and printed twice. The snippet below flushes first.
- **`waitpid(-1, ...)` with no children raises `ChildProcessError`.** A reap loop treats that as "nothing to do".
- **Unreaped children become zombies.** See `docs/concepts/zombies-and-reaping.md` (K1).

## Try it

```text
>>> import os
>>> os.getpid() > 1
True
>>> os.environ.get("HOME")
'/home/Megatron'
>>> os.environ.get("KILN_NOT_SET", "default")
'default'
>>> pid = os.fork()
>>> if pid == 0:
...     os._exit(7)
... 
>>> done_pid, status = os.waitpid(pid, 0)
>>> done_pid == pid
True
>>> os.WIFEXITED(status), os.WEXITSTATUS(status)
(True, 7)
>>> os.waitstatus_to_exitcode(status)
7
>>> pid = os.fork()
>>> if pid == 0:
...     os.kill(os.getpid(), 9)
... 
>>> _, status = os.waitpid(pid, 0)
>>> os.WIFSIGNALED(status), os.WTERMSIG(status)
(True, 9)
>>> os.waitstatus_to_exitcode(status)
-9
>>> os.waitpid(-1, os.WNOHANG)
Traceback (most recent call last):
  File "<console>", line 1, in <module>
ChildProcessError: [Errno 10] No child processes
```

(Generated from [`snippets/os-module.repl`](snippets/os-module.repl) on Python 3.15.1, Linux only.) The first child exited with code 7. The second killed itself with signal 9 (SIGKILL), and the same decoding functions tell the two cases apart. Once both were reaped, there were no children left.

A complete fork demo is in [`snippets/os-module_1.py`](snippets/os-module_1.py):

```bash
(wsl) $ ./scripts/dev.sh run docs/python/snippets/os-module_1.py
```

```text
parent: my pid is 369
child: my pid is 370, my parent is 369
parent: child 370 finished
parent: exited normally? True, exit code 3
```

The pids change on every run. The child's parent pid equals the parent's own pid: one program, two processes.

## Check yourself

1. After `pid = os.fork()`, what is `pid` in the parent and in the child?
   <details><summary>Answer</summary>

   In the parent: the child's process id (a positive number). In the child: `0`.

   </details>
2. `os.waitstatus_to_exitcode(status)` returns `-11`. What happened to the child?
   <details><summary>Answer</summary>

   It was killed by signal 11, SIGSEGV (a segmentation fault: it touched invalid memory). Negative values mean "killed by that signal".

   </details>
3. Why must a forked child end with `os._exit()` rather than `return` or `sys.exit()`?
   <details><summary>Answer</summary>

   Returning would continue running the parent's code after the `fork()` call. `sys.exit()` would run cleanup copied from the parent (its `finally` blocks and `atexit` handlers). `os._exit()` ends the child immediately and cleanly.

   </details>
