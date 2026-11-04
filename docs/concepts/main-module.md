# The Main Module: `__main__`, `__mp_main__`, and Why the Guard Matters

> **Milestone:** K2 · **Read before:** [import-system](import-system.md), [if-name-main](../python/if-name-main.md) · **Reading time:** ~10 min
> **Terms introduced:** **main module**, **`__mp_main__`**, **`__main__` guard**

## One-sentence summary

The file you run is the **main module** and gets `__name__ == "__main__"`. A spawned child imports that same file *again* under the name `__mp_main__`, so any code not protected by `if __name__ == "__main__":` runs a second time, in the child.

## Why you should care

The krb5 test script `t_otp.py` had **no `__main__` guard** (errata E-05). Under forkserver, the child re-imported the script, which:
1. ran the top-level code again, including the part that starts processes. Python refuses that with a `RuntimeError` during the child's "bootstrapping phase";
2. re-imported `k5test`, which registered an `atexit` handler that printed a bogus "re-run this test" failure banner.

The vLLM team wrote that detecting a missing guard statically "does not appear to be straightforward". forkcheck rule FA001 (`unguarded-process-start`) does it for the common cases.

## The mental model

**Analogy:** a recipe card that says "1. Preheat the oven. 2. Call a friend to come and help." Your friend reads the *whole card* when they arrive, so they preheat again and call *another* friend. The guard is a note on step 2: "only the person who started the recipe does this".

**Where the analogy fails:** the friend (the child) doesn't ignore step 1. It *must* re-run the definitions (imports, `def`s) to find the function it was asked to run. Only the "start more processes" and other side effects need guarding.

```
 parent:  python script.py      → module "__main__":    def work()… ; top-level code ; if __name__ == "__main__": start child
 child (spawn/forkserver):       → module "__mp_main__":  def work()… ; top-level code AGAIN ; guard is False → skip ✔
                                                          (without the guard: start child AGAIN → RuntimeError)
```

## See it for yourself

[`snippets/main-module_1.py`](snippets/main-module_1.py) writes a small script with an unguarded `print` at the top, and runs it. The script starts one spawn child under a guard:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/main-module_1.py
```

```text
top-level code running, __name__ = '__main__'
top-level code running, __name__ = '__mp_main__'
in the child, work() runs; this module is '__mp_main__'
```

- **Line 1:** the parent runs the script as `__main__`.
- **Line 2:** the child re-imports the *same file*, now named `__mp_main__`. The top-level `print` runs again.
- **Line 3:** inside the child, the guard was False, so it didn't start another child. It ran `work()` as asked.

What happens without the guard is shown in [`../python/snippets/if-name-main_1.py`](../python/snippets/if-name-main_1.py): the child crashes with `RuntimeError: An attempt has been made to start a new process before the current process has finished its bootstrapping phase.`

## How it really works

- When you run `python script.py`, Python executes the file in a fresh module object registered as `sys.modules["__main__"]`, with `__name__` set to `"__main__"`.
- `multiprocessing` under spawn and forkserver sends the child "preparation data", including the main script's path. The child calls `spawn._fixup_main_from_path()`, which executes that file as a new module named **`__mp_main__`** and aliases it as `__main__` in the child. That's why `pickle` can find `__main__.work` in the child: the function exists there because the file was run again.
- Python's `multiprocessing` refuses to start new processes while the child is still bootstrapping. That's the `RuntimeError` guard rail.
- **Fork is different:** the child continues from the parent's memory, so nothing is re-run and missing guards go unnoticed. That's the whole incident.

## How Kiln uses it

- Kiln's own workers don't depend on the main script. Spawned workers start with `python -m kiln.worker` ([`src/kiln/worker/__main__.py`](../../src/kiln/worker/__main__.py)) and import the *app* by its spec ([`src/kiln/launch/spawn.py`](../../src/kiln/launch/spawn.py)).
- [`src/kiln/forkcheck/symbols.py`](../../src/kiln/forkcheck/symbols.py) computes **guard regions**, the line ranges inside `if __name__ == "__main__":`. FA001 in [`src/kiln/forkcheck/rules.py`](../../src/kiln/forkcheck/rules.py) flags process starts outside them.
- [`src/kiln/forkcheck/confirm.py`](../../src/kiln/forkcheck/confirm.py) runs a script with `runpy.run_path(..., run_name="__main__")` under each start method, to *show* the behaviour changing.
- The `corpus/real/krb5-t_otp/` case ([`../../corpus/real/`](../../corpus/real/)) reproduces krb5's missing guard.

## Common misconceptions

- ❌ **Myth:** "The guard is only boilerplate for running a file as a script."
  ✅ **Reality:** under spawn and forkserver it's what stops the child from re-running your program's side effects.
- ❌ **Myth:** "Moving everything into `def main()` is enough."
  ✅ **Reality:** if `main()` is *called* at top level without a guard, it still runs in the child. krb5 found that wrapping the body in a function alone didn't help (errata E-05). The *call* must be guarded.
- ❌ **Myth:** "Imported modules don't need guards."
  ✅ **Reality:** they don't need a `__main__` guard, but their top-level side effects (`atexit.register`, starting threads) run again in every spawned child. That's forkcheck's FA005.

## Check yourself

1. In a spawned child, what is `__name__` inside the re-imported main script?
   <details><summary>Answer</summary>

   `'__mp_main__'`, as the snippet shows, so `if __name__ == "__main__":` is False there and the guarded code is skipped.

   </details>
2. Why didn't krb5's missing guard cause problems before Python 3.14?
   <details><summary>Answer</summary>

   On Linux the default was fork. A forked child continues from the parent's memory and never re-imports the script, so the unguarded code never ran twice.

   </details>
3. Predict: a script prints `"hello"` at top level (unguarded) and starts 3 spawn children under a guard. How many times does `hello` appear?
   <details><summary>Answer</summary>

   4 times: once in the parent and once in each child, because each spawned child re-imports the script.

   </details>

## Go deeper

- [Python docs: `__main__`](https://docs.python.org/3.15/library/__main__.html).
- [multiprocessing programming guidelines: "Safe importing of main module"](https://docs.python.org/3.15/library/multiprocessing.html#the-spawn-and-forkserver-start-methods).
- CPython source: `Lib/multiprocessing/spawn.py` (`get_preparation_data`, `_fixup_main_from_path`).
- [krb5 PR #1483](https://github.com/krb5/krb5/pull/1483) (errata E-05).
