# typing.Protocol and Callable

> **First used in:** see the [ledger](README.md) (milestone K1) · **Needs:** [classes-and-objects](classes-and-objects.md), [type-hints](type-hints.md) · **Reading time:** ~7 min
> **Terms introduced:** **Protocol**, **structural typing**, **duck typing**, **Callable**, **union type**

## What it looks like

```python
from typing import Protocol

class Executor(Protocol):
    capacity: int
    def inflight(self) -> int: ...

def describe(executor: Executor) -> str:
    return f"{executor.inflight()}/{executor.capacity} slots busy"
```

## What it means

A **Protocol** describes a *shape*: "any object with a `capacity` attribute and an `inflight()` method returning an int". It isn't a base class you inherit from. `SyncExecutor` never mentions `Executor`, yet mypy accepts `describe(SyncExecutor())`, because `SyncExecutor` has the right attributes and methods.

This is called **structural typing**: types match by *structure*, not by family tree. It's the type-checked version of Python's old habit called **duck typing** ("if it walks like a duck and quacks like a duck, it's a duck").

The `...` (an Ellipsis) as a method body means "no implementation here; this only describes the shape".

**`Callable`** does the same for functions: `Callable[[dict], None]` means "something you can call with one dict argument, returning nothing". Kiln uses it for callbacks such as `on_done`.

**Union types** (`A | B`) say "either an A or a B". `ClientRequest = Ping | Submit | Wait | ...` in Kiln means "a parsed client request is exactly one of these classes", and `match` or `isinstance` tells which.

## Why people use it

Kiln will have several executors (sync now; threads, interpreters and asyncio in K4) and several launchers (fork now; spawn and forkserver in K2). The code that *uses* them only cares that they have the right methods. A Protocol states that contract in one place, mypy checks every implementation against it, and the implementations don't have to inherit from anything.

## How Kiln uses it

- [`src/kiln/worker/executors/base.py`](../../src/kiln/worker/executors/base.py): `class Executor(Protocol)` with `capacity`, `submit(run, on_done)`, `inflight()` and `shutdown(wait)`. `SyncExecutor` in [`executors/sync.py`](../../src/kiln/worker/executors/sync.py) matches it without inheriting.
- `on_done: Callable[[ResultMsg], None]` is the callback an executor calls when a job finishes.
- [`src/kiln/protocol/messages.py`](../../src/kiln/protocol/messages.py) defines union types like `ClientRequest = Ping | Submit | ...`.

## Gotchas

- **Protocols are checked by mypy, not at runtime.** Passing a wrong object runs fine until a missing method is called. `isinstance(x, Executor)` even raises `TypeError` unless the Protocol is decorated with `@runtime_checkable`.
- **You can't create a Protocol instance.** `Executor()` raises `TypeError: Protocols cannot be instantiated`. It's a description, not a thing.
- **Spelling and signatures must match exactly.** A method named `in_flight` instead of `inflight`, or returning `str` instead of `int`, makes mypy reject the class wherever it's used as an `Executor`.

## Try it

```text
>>> from typing import Protocol
>>> class Executor(Protocol):
...     capacity: int
...     def inflight(self) -> int: ...
... 
>>> class SyncExecutor:
...     def __init__(self) -> None:
...         self.capacity = 1
...         self.running = 0
...     def inflight(self) -> int:
...         return self.running
... 
>>> def describe(executor: Executor) -> str:
...     return f"{executor.inflight()}/{executor.capacity} slots busy"
... 
>>> describe(SyncExecutor())
'0/1 slots busy'
>>> issubclass(SyncExecutor, Executor)
Traceback (most recent call last):
  File "<console>", line 1, in <module>
  …
TypeError: Instance and class checks can only be used with @runtime_checkable protocols
>>> from collections.abc import Callable
>>> on_done: Callable[[dict], None] = print
>>> on_done({"ok": True})
{'ok': True}
>>> Executor()
Traceback (most recent call last):
  File "<console>", line 1, in <module>
  …
TypeError: Protocols cannot be instantiated
```

(Generated from [`snippets/typing-protocol.repl`](snippets/typing-protocol.repl) on Python 3.15.1. "…" replaces frames inside the `typing` module.)

`on_done = print` works because `print` *is* something you can call with one argument. Any matching function would do.

## Check yourself

1. `SyncExecutor` doesn't inherit from `Executor`. Why does mypy still accept it where an `Executor` is expected?
   <details><summary>Answer</summary>

   Protocols use structural typing: a class matches if it has the required attributes and methods with compatible types. Inheritance isn't needed.

   </details>
2. What does `Callable[[int, str], bool]` describe?
   <details><summary>Answer</summary>

   A function (or any callable) taking an `int` and a `str`, and returning a `bool`.

   </details>
3. A teammate writes `def in_flight(self) -> int` in a new executor. When is the mistake found?
   <details><summary>Answer</summary>

   When `./scripts/dev.sh typecheck` runs: mypy reports the class doesn't match `Executor` wherever it's used as one. At runtime, nothing notices until `inflight()` is called and raises `AttributeError`.

   </details>
