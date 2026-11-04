# Subinterpreters

> **Milestone:** K4 · **Read before:** [gil](gil.md), [threads](threads.md), [pickling](pickling.md), [main-module](main-module.md) · **Reading time:** ~12 min
> **Terms introduced:** **subinterpreter**, **per-interpreter GIL**, **`concurrent.interpreters`**, **`InterpreterPoolExecutor`**, **shareable object**, **stateless function**

## One-sentence summary

A **subinterpreter** is a second, independent Python interpreter living *inside the same process*: it has its own modules, its own objects and (since 3.12) its own GIL, so subinterpreters run Python code in parallel even on the normal build, but you can only send them things that can be shared or copied.

## Why you should care

Kiln has used processes for parallelism since K1, and K4 adds threads. Subinterpreters sit in between: one process (like threads), but nearly nothing shared (like processes). Python 3.14 made them usable from Python code (`concurrent.interpreters`, PEP 734, and `InterpreterPoolExecutor`; research [§2.9](../research/python-mastery-research.md#29-concurrency-is-changing-free-threading-subinterpreters-forkserver)). K4's `interpreters` executor runs jobs in them, and the lab measures whether they really beat the GIL for CPU-bound work, and what they cost in memory.

## The mental model

**Analogy:** a shared office building (one process) with several separate companies (interpreters). Each company has its own staff, files and its own single meeting room (its own GIL), so they all work at the same time. To get something to another company you can't just hand over your folder; you send a copy, or a document they can look up in their own archive by name.

**Where the analogy fails:** a few things *are* genuinely shared, without copying: immortal objects like `None` and small ints, and special "shareable" types (bytes, strings, numbers, interpreter queues). And everything is still one process: one pid, one memory map, and one crash takes everyone down.

```
 one process (one pid)
 ┌──────────────────────────────────────────────────────────────────────┐
 │ main interpreter          subinterpreter #1          subinterpreter #2 │
 │  own GIL                   own GIL                     own GIL         │
 │  own sys.modules           own sys.modules             own sys.modules │
 │  (kiln, app, ...)          (re-imported!)              (re-imported!)  │
 │        │  submit(func, args)   ▲                                       │
 │        └──── copy / pickle ────┘   result ── copy / pickle ──▶ back    │
 └──────────────────────────────────────────────────────────────────────┘
```

## See it for yourself

[`snippets/subinterpreters_1.py`](snippets/subinterpreters_1.py) submits six different callables to an `InterpreterPoolExecutor` (Python ≥ 3.14). It changes a module global from 10 to 1000 *at run time* first:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/subinterpreters_1.py
```

```text
module body runs here with __name__ = '__main__'
lambda: 1                    -> 1
stateless(41)                -> 42
module body runs here with __name__ = '<fake __main__>'
uses_global()                -> 20
closure lambda: offset       -> NotShareableError
stateless(a threading.Lock)  -> NotShareableError
os.getpid                    -> 'same process'
main interpreter: uses_global() = 2000
```

(Identical on 3.14.9, 3.15.1 and 3.15.1t.)

- **`lambda: 1` worked.** The K4 spec expected lambdas to fail (gate G4.6); they don't, *if* they are **stateless**: no closure variables and no globals. Such a function's code can be copied into the other interpreter directly.
- **`stateless(41)`** worked for the same reason.
- **`uses_global()` reads a global**, so it can't be copied as plain code. CPython falls back to pickle, which sends it *by name* (`__main__.uses_global`, [pickling](pickling.md)). To find that name, the subinterpreter **re-ran this script** as a module called `<fake __main__>`; that's the second "module body runs" line. So it computed with the import-time value: **20, not 2000**. Exactly like spawn re-importing your main script ([main-module](main-module.md)).
- **The closure** (a lambda that captures the local `offset`) is neither stateless nor picklable: `NotShareableError`.
- **A `threading.Lock` argument** can't be copied or pickled: `NotShareableError`.
- **`os.getpid`** returns the same pid: one process.

## How it really works

- **PEP 684 (3.12): a per-interpreter GIL.** Each interpreter got its own GIL and its own copies of most runtime state, so interpreters in one process can run bytecode simultaneously. This is why `interpreters` should scale CPU-bound work even on 3.15 (GIL build).
- **PEP 734 (3.14): `concurrent.interpreters`.** `interpreters.create()`, `interp.exec(code)`, `interp.call(func, *args)`, and cross-interpreter queues. `concurrent.futures.InterpreterPoolExecutor` (gate G4.1) is a thread pool where each worker thread owns one subinterpreter and runs each task with `interp.call(...)` (`Lib/concurrent/futures/interpreter.py`).
- **How a call travels.** `Interpreter.call` (`Lib/concurrent/interpreters/__init__.py`) says: "All 'shareable' objects are supported, as are 'stateless' functions (meaning non-closures that do not use any globals). This method will fall back to pickle." So:
  1. **Shareable objects** (`None`, `bool`, `int`, `float`, `str`, `bytes`, tuples of those, interpreter queues) cross directly.
  2. **Stateless functions** are re-created from their code in the target interpreter.
  3. **Everything else is pickled:** functions and classes by qualified name (so they must be importable), other objects by value. Closures, lambdas that use globals, locks, open files and sockets fail.
  - Results and exceptions come back the same way.
- **Re-import cost.** Each subinterpreter has its own `sys.modules`. The first task that needs Kiln and the app imports them *again* inside that interpreter. Preloading in the master (K3) doesn't help, and each interpreter's copies of modules cost memory: the K4 lab measures worker RSS per executor.
- **Extensions must support it.** C extensions using single-phase initialization, or not declaring `Py_mod_multiple_interpreters` support, refuse to load in a subinterpreter with an `ImportError` ([c-extensions](c-extensions.md)). Kiln reports that as `error.type = "ImportError"` and logs `interp_import_failed`.
- **Module-level state isn't shared.** A cache dict at module level exists once per interpreter. Two jobs in two interpreters never see each other's cache.

## How Kiln uses it

`src/kiln/worker/executors/interpreters.py` (3.14+) wraps `InterpreterPoolExecutor`. It never sends the task function itself: it submits a **module-level shim** `_run_in_interpreter(app_spec, task, args_json, kwargs_json) -> str`. Inside each subinterpreter the shim imports and caches the app once, looks up the task by name, runs it and returns a JSON string. That design was chosen expecting lambdas to fail; G4.6 showed some do work, but the shim still stands: the app must be importable inside the interpreter anyway, and strings are the cheapest, most predictable thing to transfer (ADR-009: payloads are JSON).

## Common misconceptions

- ❌ **Myth:** "subinterpreters are just threads."
  ✅ **Reality:** each runs on a thread, but with its own modules, objects and GIL. Threads share everything; subinterpreters share almost nothing.
- ❌ **Myth:** "a lambda can never be sent to another interpreter."
  ✅ **Reality:** a stateless one can (measured above). It's closures, functions that use globals without being importable, and unpicklable arguments that fail.
- ❌ **Myth:** "the subinterpreter sees my program's current globals."
  ✅ **Reality:** it re-imports the module and sees import-time values (20, not 2000 above).

## Check yourself

1. Why did `uses_global()` return 20 in the subinterpreter but 2000 in the main interpreter?
   <details><summary>Answer</summary>

   It uses a global, so it was pickled by name. The subinterpreter re-ran the script (as `<fake __main__>`) to find that name, which executed `LIMIT = 10` again; the run-time change to 1000 only happened in the main interpreter's `if __name__ == "__main__":` block.

   </details>
2. Kiln's interpreters executor sends `("examples.demo:app", "add", "[1, 2]", "{}")` instead of the function object. Name two reasons.
   <details><summary>Answer</summary>

   Strings are shareable, so they cross with no pickling surprises. The task must be found by name in the subinterpreter's *own* copy of the app anyway (it re-imports everything). Bonus: it keeps payloads JSON-only, matching ADR-009.

   </details>
3. Predict: `pool.submit(lambda: open("/tmp/x", "w")).result()`. What happens? And `lambda: len("abc")`?
   <details><summary>Answer</summary>

   Builtins like `open` and `len` don't count as globals, so both lambdas are sent as stateless code and run. `len("abc")` returns 3. The `open` call also runs, but its *result*, an open file object, can't be shared or pickled back into the main interpreter: `NotShareableError: object does not support cross-interpreter data` (measured on 3.15.1). Values must cross in *both* directions.

   </details>

## Go deeper

- [PEP 684](https://peps.python.org/pep-0684/) (per-interpreter GIL) and [PEP 734](https://peps.python.org/pep-0734/) (`concurrent.interpreters`).
- [`concurrent.interpreters` docs](https://docs.python.org/3.15/library/concurrent.interpreters.html), including the list of shareable types.
- `Lib/concurrent/futures/interpreter.py`: the whole executor is about 100 lines.
