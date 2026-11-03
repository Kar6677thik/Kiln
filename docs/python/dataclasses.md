# Dataclasses

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [classes and objects](classes-and-objects.md), [type hints](type-hints.md), [decorators](decorators.md) (the `@` line) · **Reading time:** ~10 min
> **Terms introduced:** **dataclass**, **field**, **`default_factory`**, **`__repr__`**, **`__eq__`**, **frozen dataclass**

## What it looks like

```python
from dataclasses import dataclass, field


@dataclass
class CheckResult:
    check_id: str
    status: str
    detail: str = ""  # a default value
    hints: list[str] = field(default_factory=list)  # a fresh list for each instance


r = CheckResult("D1", "pass")
print(r)  # CheckResult(check_id='D1', status='pass', detail='', hints=[])
```

## What it means

Many classes exist only to **hold data**: a result, a record, a config section. Writing them by hand is repetitive, because you need an `__init__` that copies every argument onto `self`, a readable printout, and a way to compare two of them. A **dataclass** writes all of that for you.

- `@dataclass` above the class is a *decorator* (see [decorators](decorators.md)). It reads the class, then adds methods to it.
- Each line like `check_id: str` declares a **field**: a name plus a [type hint](type-hints.md). The **order matters**: it becomes the order of the parameters in the generated `__init__`.
- `detail: str = ""` gives the field a **default value**. Fields with defaults must come after fields without them, just as with function parameters.
- `field(default_factory=list)` means "call `list()` to make a **new** empty list for each instance". You can't write `hints: list = []`, because then every instance would share one list (the same trap as [mutable defaults in functions](functions.md#gotchas)). Python refuses it with a `ValueError`, as "Try it" shows.

What `@dataclass` generates for you:

| Method | What it does | Hand-written equivalent |
|---|---|---|
| `__init__` | `CheckResult("D1", "pass")` sets `self.check_id`, `self.status`, `self.detail`… | 4–5 lines of `self.x = x` |
| **`__repr__`** | how the object prints: `CheckResult(check_id='D1', ...)` | a method that builds that string |
| **`__eq__`** | `==` compares **field by field** | a method comparing every field |

A plain class without these prints something like `<Worker object at 0x7f…>`, and `==` checks whether it's the same object (see [`is` vs `==`](variables-and-types.md#equality-vs-identity--and-is)).

### Frozen dataclasses

`@dataclass(frozen=True)` makes instances **read-only** after creation. Assigning to a field raises `FrozenInstanceError`. Use it for results that should never be changed after they're made. K1 adds `slots=True` too ([spec/03 §3](../spec/03-jobs-and-tasks.md#3-the-job-record-master-side)); the K1 note `slots-and-frozen-dataclasses` explains it.

## Why people use it

- **Less code, fewer bugs.** No hand-written `__init__` with a forgotten field.
- **Readable debugging.** Printing a dataclass shows every field and its value.
- **Self-documenting.** The field list, with types, is the documentation of what the object holds.

## How Kiln uses it

- [`src/kiln/doctor.py`](../../src/kiln/doctor.py): each check returns a `CheckResult`, a **frozen** dataclass, because a finished check result should never change. The `DoctorContext` (where `/proc` is, how to run commands) is a regular dataclass, so tests can build one pointing at a fake `/proc` folder.
- [`scripts/check_docs.py`](../../scripts/check_docs.py): each problem found is a small `Problem` dataclass (file, line, check id, message).
- From K1, `Config` is a tree of frozen dataclasses ([spec/05 §1](../spec/05-configuration-and-cli.md#1-where-settings-come-from-and-who-wins)), and `Job` and `WorkerHandle` are dataclasses ([spec/03 §3](../spec/03-jobs-and-tasks.md#3-the-job-record-master-side), [spec/04 §2](../spec/04-supervisor.md#2-workerhandle-the-masters-record-of-one-worker)).

## Gotchas

- **Mutable defaults need `default_factory`.** `items: list = []` is refused. Use `field(default_factory=list)`.
- **Type hints aren't enforced.** `CheckResult(check_id=5, status=None)` runs fine. The hints document intent, and mypy checks them, but Python doesn't (see [type hints](type-hints.md)).
- **A field without a type hint isn't a field.** `detail = ""` (no `: str`) becomes an ordinary class attribute, not a constructor parameter.
- **Order of defaults.** A field without a default can't come after one with a default, or you get a `TypeError` when the class is defined.

## Try it

```text
>>> from dataclasses import dataclass, field
>>> @dataclass
... class CheckResult:
...     check_id: str
...     status: str
...     detail: str = ""
... 
>>> r = CheckResult("D1", "pass")
>>> r
CheckResult(check_id='D1', status='pass', detail='')
>>> r.status
'pass'
>>> r == CheckResult("D1", "pass")
True
>>> r == CheckResult("D1", "fail")
False
>>> @dataclass
... class Job:
...     task: str
...     args: list = field(default_factory=list)
... 
>>> a = Job("add")
>>> b = Job("add")
>>> a.args.append(1)
>>> a
Job(task='add', args=[1])
>>> b
Job(task='add', args=[])
>>> @dataclass
... class Broken:
...     items: list = []
... 
Traceback (most recent call last):
  File "<console>", line 1, in <module>
  File "/home/Megatron/.local/share/uv/python/cpython-3.15.1-linux-x86_64-gnu/lib/python3.15/dataclasses.py", line 1462, in dataclass
    return wrap(cls)
  File "/home/Megatron/.local/share/uv/python/cpython-3.15.1-linux-x86_64-gnu/lib/python3.15/dataclasses.py", line 1452, in wrap
    return _process_class(cls, init, repr, eq, order, unsafe_hash,
                          frozen, match_args, kw_only, slots,
                          weakref_slot)
  File "/home/Megatron/.local/share/uv/python/cpython-3.15.1-linux-x86_64-gnu/lib/python3.15/dataclasses.py", line 1083, in _process_class
    cls_fields.append(_get_field(cls, name, type, kw_only))
                      ~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/Megatron/.local/share/uv/python/cpython-3.15.1-linux-x86_64-gnu/lib/python3.15/dataclasses.py", line 910, in _get_field
    raise ValueError(f'mutable default {type(f.default)} for field '
                     f'{f.name} is not allowed: use default_factory')
ValueError: mutable default <class 'list'> for field items is not allowed: use default_factory
>>> @dataclass(frozen=True)
... class Point:
...     x: int
...     y: int
... 
>>> p = Point(1, 2)
>>> p.x = 5
Traceback (most recent call last):
  File "<console>", line 1, in <module>
  File "<string>", line 16, in __setattr__
dataclasses.FrozenInstanceError: cannot assign to field 'x'
```

Things to notice:
- `r` printed with every field, and that came free from the generated `__repr__`.
- `==` compared field values.
- `a` and `b` each got their **own** list, thanks to `default_factory`.
- The `Broken` traceback is long because the error is raised deep inside the `dataclasses` module. **Read a traceback from the bottom**: the last line tells you what went wrong and how to fix it ("use default_factory"). See [exceptions](exceptions.md).
- `File "<string>", line 16, in __setattr__` shows that the dataclass machinery *generated* the `__setattr__` method as code at run time. That's why it has no real file name.

## Check yourself

1. What three methods does a plain `@dataclass` write for you?
   <details><summary>Answer</summary>

   `__init__` (the constructor), `__repr__` (the printout) and `__eq__` (field-by-field `==`).

   </details>
2. Why does `items: list = []` fail, and what do you write instead?
   <details><summary>Answer</summary>

   One list would be shared by every instance, a classic bug. The dataclass machinery refuses it. Write `items: list = field(default_factory=list)` so each instance gets a fresh list.

   </details>
3. Why is `doctor.py`'s `CheckResult` frozen?
   <details><summary>Answer</summary>

   A check's result is a finished fact. Making it read-only means no later code can accidentally change a result after the check produced it.

   </details>
