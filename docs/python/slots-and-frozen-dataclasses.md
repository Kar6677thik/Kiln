# Slots and frozen dataclasses

> **First used in:** see the [ledger](README.md) (milestone K1) · **Needs:** [dataclasses](dataclasses.md), [classes-and-objects](classes-and-objects.md) · **Reading time:** ~7 min
> **Terms introduced:** **slots**, **`__slots__`**, **`__dict__`**, **FrozenInstanceError**, **hashable**

## What it looks like

```python
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class WorkerConfig:
    workers: int = 4
    max_jobs: int = 0
```

## What it means

Two switches on the `@dataclass` decorator (see [dataclasses](dataclasses.md)):

- **`frozen=True`**: after the object is created, its fields can't be changed. Assigning `cfg.workers = 8` raises `dataclasses.FrozenInstanceError`. To get a changed copy, use `dataclasses.replace(cfg, workers=8)`, which leaves the original untouched.
- **`slots=True`**: normally every Python object keeps its attributes in a hidden dictionary called **`__dict__`**, so you can add *any* attribute at any time. With slots, the class declares a fixed list of attribute names (**`__slots__`**) and stores them in fixed positions instead. The object has no `__dict__`, uses less memory, and is slightly faster to access.

Together: a small, fixed-shape, read-only record.

A frozen dataclass is also **hashable**: it can be a dictionary key or a set member, because its value can never change after it's been put in the dict.

## Why people use it

- **Frozen** protects data that must not change after it's been checked. Kiln validates the config once at startup; if any code could later write `config.workers = -3`, that validation would mean nothing.
- **Slots** catch typos and save memory. On a *plain* dataclass, `loose.wrokers = 8` (misspelled) silently creates a new attribute, and the real `workers` stays 4. On a slotted (or frozen) class it's an error. And when you keep tens of thousands of records (Kiln's job table holds up to 200,000 jobs), dropping the per-object `__dict__` adds up.

## How Kiln uses it

- [`src/kiln/config.py`](../../src/kiln/config.py): every config section is `@dataclass(frozen=True, slots=True)`. The loaded `Config` can't be modified by accident ([spec/05 §1](../spec/05-configuration-and-cli.md#1-where-settings-come-from-and-who-wins)).
- [`src/kiln/protocol/messages.py`](../../src/kiln/protocol/messages.py): parsed requests (`Submit`, `Wait`, ...) are frozen slotted dataclasses. Once a message is validated, nothing can change it afterwards.
- [`src/kiln/master/jobs.py`](../../src/kiln/master/jobs.py): `Job` uses `slots=True` but **not** frozen, because a job's state changes over its life, through one function only (spec/03 §4).

## Gotchas

- **Frozen means shallow.** `frozen=True` stops you from reassigning a field, but if the field holds a list, the list itself can still be changed (`cfg.items.append(...)`). Kiln's config uses tuples and frozensets for collections to avoid this.
- **The error for a typo on a frozen class mentions "field".** `cfg.typo = 1` says `cannot assign to field 'typo'`, even though `typo` isn't a field. Read it as "you can't assign anything here".
- **`replace` re-runs `__init__`**, so any validation in `__post_init__` runs again for the copy. That's usually what you want.

## Try it

```text
>>> from dataclasses import dataclass, replace
>>> @dataclass(frozen=True, slots=True)
... class WorkerConfig:
...     workers: int = 4
...     max_jobs: int = 0
... 
>>> cfg = WorkerConfig()
>>> cfg
WorkerConfig(workers=4, max_jobs=0)
>>> cfg.workers = 8
Traceback (most recent call last):
  File "<console>", line 1, in <module>
  File "<string>", line 16, in __setattr__
dataclasses.FrozenInstanceError: cannot assign to field 'workers'
>>> cfg2 = replace(cfg, workers=8)
>>> cfg2
WorkerConfig(workers=8, max_jobs=0)
>>> cfg
WorkerConfig(workers=4, max_jobs=0)
>>> cfg.typo = 1
Traceback (most recent call last):
  File "<console>", line 1, in <module>
  File "<string>", line 16, in __setattr__
dataclasses.FrozenInstanceError: cannot assign to field 'typo'
>>> @dataclass
... class Loose:
...     workers: int = 4
... 
>>> loose = Loose()
>>> loose.wrokers = 8
>>> loose
Loose(workers=4)
>>> hasattr(cfg, "__dict__"), hasattr(loose, "__dict__")
(False, True)
>>> {cfg: "usable as a dict key"}
{WorkerConfig(workers=4, max_jobs=0): 'usable as a dict key'}
```

(Generated from [`snippets/slots-and-frozen-dataclasses.repl`](snippets/slots-and-frozen-dataclasses.repl) on Python 3.15.1. `File "<string>"` appears because dataclasses generate the `__setattr__` method as code at class-creation time.)

Look at `loose.wrokers = 8`: no error, and `loose` still shows `workers=4`. That silent typo is exactly what slots and frozen prevent.

## Check yourself

1. How do you get a frozen config with `max_jobs=100` from an existing `cfg`?
   <details><summary>Answer</summary>

   `new = dataclasses.replace(cfg, max_jobs=100)`. It builds a new object; `cfg` itself stays unchanged.

   </details>
2. Why is `Job` slotted but not frozen?
   <details><summary>Answer</summary>

   A job changes state (QUEUED → RUNNING → SUCCEEDED...), so its fields must be writable. Slots still give a fixed shape (no typo attributes) and save memory across many jobs.

   </details>
3. On a plain (non-slotted, non-frozen) dataclass, what does `obj.missspelled = 1` do?
   <details><summary>Answer</summary>

   It silently creates a new attribute in the object's `__dict__`. Nothing complains, and the real field keeps its old value.

   </details>
