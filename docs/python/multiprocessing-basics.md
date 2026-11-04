# multiprocessing basics (enough to read the K2 corpus)

> **First used in:** see the [ledger](README.md) (milestone K2) · **Needs:** [if-name-main](if-name-main.md), [pickle-and-reduce](pickle-and-reduce.md) · **Reading time:** ~7 min
> **Terms introduced:** **multiprocessing**, **`Process`**, **`Pool`**, **`get_context`**, **`set_start_method`**

## What it looks like

```python
import multiprocessing as mp

def square(n):
    return n * n

if __name__ == "__main__":
    p = mp.Process(target=square, args=(3,))   # one child process running square(3)
    p.start(); p.join()
    with mp.Pool(processes=2) as pool:         # a pool of worker processes
        print(pool.map(square, [1, 2, 3]))     # → [1, 4, 9]
```

## What it means

- **`multiprocessing`** is the standard library's way to run Python code in other processes.
- **`Process(target=f, args=(...))`** describes one child. `.start()` creates it, and `.join()` waits for it to finish. `.exitcode` tells you how it ended.
- **`Pool(processes=n)`** starts *n* worker processes. `.map(f, items)` sends `f` and the items to the workers and collects the results in order. There are also `.apply_async`, `.imap` and `.starmap`.
- **Start methods:** `mp.set_start_method("spawn")` picks the method for the whole program. Call it once, under the main guard. **`mp.get_context("fork")`** returns an object with the same API (`.Process`, `.Pool`, `.Queue`) bound to one method, without changing the global setting. See [start-methods](../concepts/start-methods.md).

## Why people use it

To use several CPU cores for CPU-bound Python work on GIL builds, where threads can't run Python code in parallel (K4). It's the most common reason real projects hit start-method breakage.

## How Kiln uses it

- **Kiln's launchers do not use multiprocessing** ([ADR-012](../decisions.md#adr-012)). They use `os.fork`, `os.posix_spawn` and a hand-written forkserver ([`src/kiln/launch/`](../../src/kiln/launch/fork.py)), so the mechanics stay visible.
- multiprocessing appears in the code forkcheck *analyses*: the corpus in [`corpus/real/`](../../corpus/real/), whose `Process`/`Pool` calls [`src/kiln/forkcheck/scanner.py`](../../src/kiln/forkcheck/scanner.py) recognises. It's also used in [`src/kiln/forkcheck/confirm.py`](../../src/kiln/forkcheck/confirm.py), which calls `set_start_method` before running a script.

## Gotchas

- **Use the main guard.** Under spawn and forkserver the main script is re-imported in every child ([if-name-main](if-name-main.md)).
- **Targets and arguments must be picklable** under spawn and forkserver. `Pool.map` pickles under *every* method ([pickling](../concepts/pickling.md)).
- **Module-level values are recomputed in spawned children.** In "Try it", `PARENT_PID = os.getpid()` is re-run in the spawn child, so the child compares its own pid with itself and prints `False`. A forked child keeps the parent's value and prints `True`. That's the "state not inherited" lesson in one line.
- **`set_start_method` can be called only once** (pass `force=True` to override). Prefer `get_context(...)` in libraries.

## Try it

[`snippets/multiprocessing-basics_1.py`](snippets/multiprocessing-basics_1.py), run on 3.15.1 and 3.13.17 (`./scripts/dev.sh run docs/python/snippets/multiprocessing-basics_1.py`):

```text
default start method here: forkserver
  hello fork from pid-is-different=True
  fork: child exit code 0
  hello spawn from pid-is-different=False
  spawn: child exit code 0
Pool.map(square, [1, 2, 3, 4]) = [1, 4, 9, 16]
```

On 3.13.17 the first line is `default start method here: fork`. Everything else is identical.

## Check yourself

1. Why does the spawn child print `pid-is-different=False`?
   <details><summary>Answer</summary>

   The spawn child re-imports the script, so `PARENT_PID = os.getpid()` runs again *in the child* and stores the child's own pid. The fork child is a memory copy and keeps the parent's value.

   </details>
2. What's the difference between `mp.set_start_method("spawn")` and `mp.get_context("spawn")`?
   <details><summary>Answer</summary>

   `set_start_method` changes the default for the whole program (once). `get_context` returns a separate object whose `.Process`/`.Pool` use that method, leaving the default alone.

   </details>
3. Predict: does `mp.get_context("fork").Pool().map(lambda x: x, [1])` work?
   <details><summary>Answer</summary>

   No. A Pool sends the function to already-running workers by pickling it, even under fork, and a lambda can't be pickled. `Process(target=lambda: 1)` under fork *does* work, because nothing is pickled.

   </details>
