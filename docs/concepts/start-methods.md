# Start Methods: fork, spawn and forkserver

> **Milestone:** K2 · **Read before:** [fork](fork.md), [fork-and-threads](fork-and-threads.md), [main-module](main-module.md) · **Reading time:** ~15 min
> **Terms introduced:** **start method**, **spawn**, **forkserver**, **preload**

## One-sentence summary

A **start method** is *how* a new worker process gets made: **fork** copies the running parent, **spawn** starts a brand-new Python that imports everything again, and **forkserver** asks a small clean helper process to fork a copy of *itself*.

## Why you should care

For about 20 years, Python on Linux created child processes with `fork`, and a lot of code silently depended on the child being an exact copy of the parent. In **Python 3.14** the Linux default became `forkserver` (research §2.9), and that code broke. The krb5 test suite re-ran its own script in the child. oslo.concurrency's nested target functions couldn't be sent to the child. FSL pyfeeds lost its logging setup. All three are reproduced in Kiln's corpus (`corpus/real/`), and K2's `kiln forkcheck` exists to find this kind of hidden assumption *before* it breaks.

Kiln itself can start workers all three ways (`start.method` in [spec/05](../spec/05-configuration-and-cli.md)), so you can watch the differences directly.

## The mental model

**Analogy:** you need a new chef for your kitchen.
- **fork** clones your best chef on the spot. The clone already knows every recipe, and remembers every half-finished thought, including the pot they were "about to stir".
- **spawn** hires someone new from the street and hands them the recipe book. They're clean, but slow to get going: they read the whole book (import everything) before cooking.
- **forkserver**: early in the morning you trained one apprentice who has read only the basics. Whenever you need a chef, you clone the *apprentice*, never yourself. The clones are fast and clean, but they don't know what *you* learned after the morning.

**Where the analogy fails:** a fork clone doesn't really copy everything at once. The memory is shared copy-on-write until someone writes to it (K3). The clone also has exactly *one* thread, the one that called fork.

```
  fork                         spawn                          forkserver
  ────                         ─────                          ──────────
  master ──fork()──▶ worker    master ──posix_spawn──▶ python   master ──posix_spawn (once)──▶ forkserver
  (full copy: memory, fds,     (fresh interpreter: re-imports   (clean helper: imported only the
   handlers, globals AS THEY    the app; gets only fds we mark   preload list)
   ARE NOW)                     inheritable; config via args)      master ──"spawn_worker" + fd──▶ forkserver
                                                                   forkserver ──fork()──▶ worker
                                                                   (copy of the HELPER, not of the master)
```

## See it for yourself

[`snippets/start-methods_1.py`](snippets/start-methods_1.py) changes a module-level variable *at run time* in the parent, then starts a child under each method and asks the child what it sees:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/start-methods_1.py
```

```text
fork       child saw: 'changed by the parent at run time'    start+join took   35.6 ms
forkserver child saw: 'import-time value'                    start+join took  295.4 ms
spawn      child saw: 'import-time value'                    start+join took  187.7 ms
```

- **fork:** the child is a copy of the parent *as it is right now*, so it sees the change. It's also by far the fastest.
- **forkserver** and **spawn:** the child imports the file fresh, so it sees the value written in the source. That's "runtime global not inherited", forkcheck rule FA004.
- **Timing:** this forkserver number includes starting the helper process itself, which happens once. Later forkserver starts are fast forks of the helper. K2's lab measures the steady-state costs properly.

The default differs by Python version. Measured on this machine (`python -c "import multiprocessing as m; print(m.get_start_method())"`):

| Interpreter | Default start method on Linux |
|---|---|
| 3.13.17 | `fork` |
| 3.14.9 | `forkserver` |
| 3.15.1 | `forkserver` |
| 3.15.1t | `forkserver` |

On macOS and Windows the default has been `spawn` for years (Windows has no fork at all).

## How it really works

| | fork | spawn | forkserver |
|---|---|---|---|
| **System calls** | `fork(2)` in the parent | `posix_spawn(3)` → `exec` of a new `python` | `posix_spawn` once for the server, then `fork(2)` *in the server* for every worker |
| **Memory** | a copy of the parent (copy-on-write) | nothing shared; everything re-imported | a copy of the server's small memory |
| **Open fds** | *all* of the parent's (the child must close the ones it shouldn't keep) | only fds marked inheritable (PEP 446) | only the fd sent over the socket ([fd-passing](fd-passing.md)) |
| **Threads** | only the calling thread survives; other threads' locks may be stuck ([fork-and-threads](fork-and-threads.md)) | fresh, no inherited threads | the server has no threads, so it's safe |
| **Module-level code** | not re-run | re-run: the main script is imported as `__mp_main__` ([main-module](main-module.md)) | re-run for modules not in the preload list |
| **Arguments and target** | nothing needs pickling for `Process` | must be pickled ([pickling](pickling.md)) | must be pickled |
| **Who is the parent** | the master | the master | the **forkserver**, which changes who can `waitpid` ([reparenting-and-orphans](reparenting-and-orphans.md)) |

**Preload** means importing modules *before* the copies are made, so every copy shares those pages instead of importing them itself. Under fork, Kiln preloads the app in the master (`app.preload = true`). Under forkserver it preloads in the helper (`start.forkserver_preload`), keeping the master clean. Under spawn, preloading is impossible, because every worker is a fresh interpreter.

**Why did 3.14 switch?** `fork` copies only the calling thread. If any other thread held a lock at that moment (inside logging, malloc or an import), the child inherits the lock *locked*, with no thread that will ever release it. Since 3.12, Python warns when you fork a multi-threaded process. forkserver avoids the risk, because the helper is single-threaded by design.

## How Kiln uses it

- [`src/kiln/launch/base.py`](../../src/kiln/launch/base.py): the `Launcher` protocol all three methods implement.
- [`src/kiln/launch/fork.py`](../../src/kiln/launch/fork.py): `os.fork()` in the master (K1's launcher, behind the protocol).
- [`src/kiln/launch/spawn.py`](../../src/kiln/launch/spawn.py): `os.posix_spawn` of `python -m kiln.worker` ([`src/kiln/worker/__main__.py`](../../src/kiln/worker/__main__.py)), passing the channel fd and the config as arguments.
- [`src/kiln/launch/forkserver.py`](../../src/kiln/launch/forkserver.py) and [`src/kiln/launch/forkserver_main.py`](../../src/kiln/launch/forkserver_main.py): the master-side client and the helper process.
- Code tag: `CONCEPT(start-methods)`.

## Common misconceptions

- ❌ **Myth:** "spawn is just a slower fork."
  ✅ **Reality:** it's a different *contract*. The child gets none of the parent's runtime state: no changed globals, no logging handlers, no patches. Everything it needs must be importable or picklable.
- ❌ **Myth:** "forkserver children are copies of my program."
  ✅ **Reality:** they're copies of the *helper*, which only imported its preload list. Anything your program did after starting the helper is invisible to them.
- ❌ **Myth:** "If my code works on Linux, it works everywhere."
  ✅ **Reality:** until 3.14 Linux used fork while macOS and Windows used spawn. Plenty of "Linux-only" bugs were really "fork-only assumptions".

## Check yourself

1. A parent sets `CONFIG["db"] = "prod"` at run time, then starts a child under each method. Which children see `"prod"`?
   <details><summary>Answer</summary>

   Only the **fork** child, which copies the parent's memory as it is at that moment. spawn and forkserver children import the module fresh and see the import-time value. That's the snippet above, and forkcheck rule FA004.

   </details>
2. Why is forkserver usually much faster than spawn per worker, once it's running?
   <details><summary>Answer</summary>

   Each new worker is a `fork()` of an already-running interpreter that has already imported its preload list. spawn starts a whole new interpreter and imports everything from scratch for every worker.

   </details>
3. Which start method makes the master *not* the parent of its workers, and what problem does that create?
   <details><summary>Answer</summary>

   forkserver: the helper forks the workers, so they're the helper's children. Only a parent can `waitpid` a child, so the helper must reap them and report their exit status back to the master (spec/02 §4.3). See [reparenting-and-orphans](reparenting-and-orphans.md).

   </details>
4. Predict: on Python 3.13 on Linux, `multiprocessing.get_start_method()` prints what? And on 3.14?
   <details><summary>Answer</summary>

   `fork` on 3.13, and `forkserver` on 3.14 and later (measured above). That single change is the incident this milestone studies.

   </details>

## Go deeper

- [Python docs: Contexts and start methods](https://docs.python.org/3.15/library/multiprocessing.html#contexts-and-start-methods): the official comparison and the 3.14 default change.
- [What's New in Python 3.14](https://docs.python.org/3/whatsnew/3.14.html): the default start method change on Linux.
- CPython source: `Lib/multiprocessing/popen_fork.py`, `popen_spawn_posix.py`, `popen_forkserver.py`, `forkserver.py`.
- `man 2 fork`, `man 3 posix_spawn`.
