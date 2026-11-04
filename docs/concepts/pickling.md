# Pickling: Turning Python Objects into Bytes (and Why Some Can't Be)

> **Milestone:** K2 · **Read before:** [start-methods](start-methods.md), [closures](../python/closures.md) · **Reading time:** ~12 min
> **Terms introduced:** **pickling**, **unpickling**, **pickle by reference**, **qualified name (qualname)**, **picklable**

## One-sentence summary

**Pickling** turns a Python object into bytes so it can be sent to another process (or saved), and **unpickling** rebuilds it there. Functions and classes travel *by name*, so anything without a findable name can't be pickled.

## Why you should care

Under spawn and forkserver, a new process shares no memory with its parent. Everything the child needs to *start* (the target function and its arguments) has to travel as bytes, and Python's `multiprocessing` uses pickle for that. oslo.concurrency's tests passed **nested functions** as process targets. That worked for years under fork, where nothing is sent, and failed the day the default became forkserver (errata E-06). Kiln's forkcheck rules FA002 (`unpicklable-target`) and FA007 (`unpicklable-argument`) catch exactly this.

Kiln itself sends **JSON**, not pickle (ADR-009). Unpickling untrusted bytes can run arbitrary code, and that's not a risk worth taking for a job server.

## The mental model

**Analogy:** shipping furniture. A table can be packed flat and rebuilt from the parts. A *recipe* isn't shipped at all: you write "page 42 of the cookbook everyone owns", and the other side looks it up in their copy. Functions are pickled like the recipe. The bytes say "module `work`, name `square`", and the receiver imports that module and fetches the name.

**Where the analogy fails:** if the receiver's cookbook has no page 42 (the function is nested inside another function, or is a lambda), the lookup fails. And some objects, like an open lock or a socket, are tied to *this* process and can't be described in bytes at all.

```
 parent                               bytes on the wire                     child
 square (module-level) ──pickle──▶ b"...\x8c\x04work\x8c\x06square\x93." ──unpickle──▶ import work; work.square ✅
 nested (inside test()) ──pickle──▶ needs "test.<locals>.nested": not findable by import ──▶ ❌ PicklingError
 threading.Lock()       ──pickle──▶ a lock is process state, not data        ──▶ ❌ TypeError
```

## See it for yourself

[`snippets/pickling_1.py`](snippets/pickling_1.py) tries to pickle five things:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/pickling_1.py
```

```text
module-level function  OK    40 bytes  contains name? True
nested function        FAIL PicklingError: Can't pickle local object <function make_nested.<locals>.nested at 0x7dfdde96a980>
lambda                 FAIL PicklingError: Can't pickle <function <lambda> at 0x7dfdde96aa30>: it's not found as __main__.<lambda>
threading.Lock()       FAIL TypeError: cannot pickle '_thread.lock' object
dict of numbers        OK    34 bytes  contains name? False
qualname of the nested function: make_nested.<locals>.nested
```

- The module-level function pickles to only 40 bytes, and those bytes **contain its name**. The code itself isn't in there.
- The nested function's qualified name, `make_nested.<locals>.nested`, says "a local variable inside a call". No import can find it, so pickle refuses.
- The lambda's name is `<lambda>`, which is also not findable.
- The lock is a live operating-system object, so pickle refuses it with a `TypeError`.
- Plain data (dicts, lists, numbers, strings) pickles by value.

## How it really works

- `pickle.dumps(obj)` walks the object and writes **opcodes**: a tiny program for rebuilding it.
  - Plain data is written by value.
  - A **function or class** is written as the opcode `STACK_GLOBAL` with two strings, the module name and the **qualified name** (`__qualname__`). That's **pickle by reference**.
  - Unpickling does `importlib.import_module(module)`, then walks the qualname.
  - Before writing, pickle checks that the lookup would really return the same object. That's why nested functions and lambdas fail *at pickling time*.
- Instances of ordinary classes are pickled as "the class (by reference) + its `__dict__`". A class can customise this with **`__reduce__`**, which returns "call this callable with these arguments to rebuild me". The `copyreg` module registers reducers for types you don't own.
- **Why unpickling is dangerous:** `__reduce__` can name *any* callable, for example `os.system` with a shell command. `pickle.loads` on attacker-controlled bytes runs attacker-chosen code. That's why Kiln's protocol is JSON (ADR-009), and the Python docs warn: "never unpickle data received from an untrusted source".
- `multiprocessing` under spawn and forkserver pickles the `Process` target, its args and kwargs. `Pool.map` pickles the function and every item **even under fork**, because it sends them over a pipe to already-running workers. forkcheck's FA002 marks Pool sites as breaking under all three methods.

## How Kiln uses it

- Kiln **doesn't** pickle jobs: arguments and results are JSON (ADR-009, [spec/03 §2.1](../spec/03-jobs-and-tasks.md)).
- Kiln's spawn launcher doesn't pickle anything either. It passes the config as JSON on the command line (`Config.to_json()`, [`src/kiln/launch/spawn.py`](../../src/kiln/launch/spawn.py)).
- [`src/kiln/forkcheck/rules.py`](../../src/kiln/forkcheck/rules.py) FA002 and FA007 flag *other people's* code that will hit pickling failures. The `corpus/real/oslo-lockutils/` case ([`../../corpus/real/`](../../corpus/real/)) reproduces the oslo breakage.

## Common misconceptions

- ❌ **Myth:** "pickle saves the function's code."
  ✅ **Reality:** it saves the function's *name*. The receiving process must be able to import it. Change the function in the source, and the unpickled reference uses the new code.
- ❌ **Myth:** "If pickling works under fork, it works under spawn."
  ✅ **Reality:** `Process(target=...)` isn't pickled at all under fork, so a fork test proves nothing about picklability.
- ❌ **Myth:** "pickle is a safe data format, like JSON."
  ✅ **Reality:** unpickling can run arbitrary code. Only unpickle bytes you produced yourself.

## Check yourself

1. Why does `pickle.dumps(make_nested())` fail even though the function works fine when called?
   <details><summary>Answer</summary>

   Functions are pickled by module + qualified name, and its qualname is `make_nested.<locals>.nested`. `<locals>` means "a local variable of some call", which no import can look up, so pickle refuses rather than produce bytes that can't be unpickled.

   </details>
2. Predict: does `mp.get_context("fork").Process(target=lambda: 1).start()` work? What about the same with `"spawn"`?
   <details><summary>Answer</summary>

   It works with fork, because the child is a memory copy and nothing is pickled. It fails with spawn, because the lambda must be pickled and can't be. That's why forkcheck reports FA002 at `info` when an explicit fork context is used.

   </details>
3. Why is `Pool.map(nested, xs)` broken even under fork?
   <details><summary>Answer</summary>

   A Pool's workers are already running when you call `map`, so the function and the items must be *sent* to them through a pipe, which means pickling, whatever the start method.

   </details>
4. Why does Kiln send JSON instead of pickle between processes?
   <details><summary>Answer</summary>

   Unpickling untrusted data can execute arbitrary code, through `__reduce__` naming any callable. Pickle also ties both sides to the same Python and the same class definitions. JSON carries only data (ADR-009).

   </details>

## Go deeper

- [Python docs: pickle](https://docs.python.org/3.15/library/pickle.html), especially "What can be pickled and unpickled?" and the security warning.
- [Python docs: multiprocessing programming guidelines](https://docs.python.org/3.15/library/multiprocessing.html#programming-guidelines) ("Picklability").
- Python note: [pickle-and-reduce](../python/pickle-and-reduce.md).
- CPython source: `Lib/pickle.py` (`save_global`), `Lib/copyreg.py`.
