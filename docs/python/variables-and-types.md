# Variables and Types

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [running Python and the REPL](running-python-and-the-repl.md) · **Reading time:** ~12 min
> **Terms introduced:** **object**, **variable (name)**, **type**, **int**, **float**, **str**, **bool**, **None**, **list**, **dict**, **tuple**, **set**, **mutable / immutable**, **identity (`is`)**, **equality (`==`)**

## What it looks like

```python
workers = 4  # an int
timeout_s = 2.5  # a float
bind = "127.0.0.1:7340"  # a str (text)
labs_enabled = False  # a bool
pidfile = None  # "no value"
tags = ["fast", "small"]  # a list
config = {"workers": 4, "bind": bind}  # a dict
point = (3, 4)  # a tuple
ids = {1, 2, 3}  # a set
```

## What it means

**Everything Python works with is an object**: a number, a piece of text, a list, even a function. An **object** is a value living in memory, and it knows its own **type**, meaning what kind of thing it is and what you can do with it.

A **variable** is a **name** that points at an object. `workers = 4` means "make the name `workers` point at the int object `4`". The `=` doesn't mean "equals" as in maths. Read it as "now refers to".

```
 names                objects in memory
 ─────               ───────────────────
 workers ─────────▶   4        (int)
 a ───────┐
          ├────────▶  [1, 2, 3] (list)
 b ───────┘
```

Two names can point at the **same** object, as `a` and `b` do here. That turns out to matter a lot (see Gotchas).

### The built-in types you'll meet everywhere

| Type | Example | What it's for |
|---|---|---|
| **int** | `42`, `-3`, `1_000_000` | whole numbers, of any size |
| **float** | `2.5`, `1e-3` | numbers with a fractional part (approximate!) |
| **str** | `"kiln"`, `'hi'` | text. Single and double quotes mean the same |
| **bool** | `True`, `False` | yes or no |
| **None** | `None` | "nothing here", for example "no pidfile configured" |
| **list** | `[10, 20, 30]` | an ordered collection you can change. Positions start at 0 |
| **dict** | `{"workers": 4}` | a lookup table from **keys** to **values** ("dictionary") |
| **tuple** | `(3, 4)` | an ordered collection you *can't* change |
| **set** | `{1, 2, 3}` | an unordered collection with no duplicates |

`type(x)` tells you an object's type. `x[0]` reads position 0 of a list or tuple, and `d["key"]` reads a dict entry.

### Mutable vs. immutable

- **Mutable** objects can be changed in place: list, dict, set. `b.append(3)` changes the list object itself.
- **Immutable** objects can never change: int, float, str, bool, None, tuple. `s.upper()` doesn't change `s`; it makes a **new** string. Trying to assign into a tuple is an error.

### Equality vs. identity: `==` and `is`

- **`a == b`** (**equality**) asks: do these two objects have the same *value*?
- **`a is b`** (**identity**) asks: are these two names pointing at the *very same object*?

Use `==` for comparing values. Use `is` almost only for `x is None`, because there is exactly one `None` object.

## Why people use it

You need names for the things your program works with. Built-in types cover most needs: counts (`int`), durations (`float`), text (`str`), flags (`bool`), "not set" (`None`), sequences (`list`, `tuple`), lookups (`dict`) and membership (`set`). JSON, the format Kiln's protocol uses, maps exactly onto `dict`, `list`, `str`, `int`/`float`, `bool` and `None` (see [spec/02](../spec/02-protocol.md)).

## How Kiln uses it

- [`src/kiln/bench/records.py`](../../src/kiln/bench/records.py) builds an ExperimentRecord as a plain **dict** of strings, numbers, lists and nested dicts. That's exactly the shape JSON can store ([spec/10 §2](../spec/10-measurement-methodology.md#2-the-experimentrecord-one-json-file-per-run)).
- [`src/kiln/__init__.py`](../../src/kiln/__init__.py) holds a single **str**, `__version__ = "0.1.0"`.
- From K1, every job's `args` is a **list** and its `kwargs` a **dict** ([spec/03](../spec/03-jobs-and-tasks.md)). Remember the rule from there: tuples arrive as lists, because JSON has no tuples.

## Gotchas

- **Two names, one object.** After `b = a`, changing the list through `b` changes what `a` sees too (see "Try it"). If you want a separate copy, write `b = list(a)`.
- **`is` on numbers or strings is unreliable.** `a == c` is the right question. `a is c` depends on whether Python happened to reuse an object, and it can differ between versions.
- **Floats are approximate.** `0.1 + 0.2 == 0.3` is `False` (it's `0.30000000000000004`). Kiln stores durations as floats but never compares them with `==`.
- **Sets have no order.** Never rely on the order a set prints in, especially for strings.
- **`True` and `False` start with capitals**, and so does `None`. `true` is a `NameError`.

## Try it

```text
>>> x = 42
>>> type(x)
<class 'int'>
>>> price = 9.99
>>> type(price)
<class 'float'>
>>> name = "kiln"
>>> type(name)
<class 'str'>
>>> ready = True
>>> type(ready)
<class 'bool'>
>>> nothing = None
>>> print(nothing)
None
>>> workers = [10, 20, 30]
>>> workers[0]
10
>>> config = {"workers": 4, "bind": "127.0.0.1:7340"}
>>> config["workers"]
4
>>> point = (3, 4)
>>> point[1]
4
>>> point[0] = 10
Traceback (most recent call last):
  File "<console>", line 1, in <module>
TypeError: 'tuple' object does not support item assignment
>>> ids = {3, 1, 3, 2}
>>> ids
{1, 2, 3}
>>> a = [1, 2]
>>> b = a
>>> b.append(3)
>>> a
[1, 2, 3]
>>> c = [1, 2, 3]
>>> a == c
True
>>> a is c
False
>>> a is b
True
>>> s = "kiln"
>>> s.upper()
'KILN'
>>> s
'kiln'
```

Things to notice:
- `workers[0]` is `10`, because positions start at **0**.
- Assigning into the tuple fails with a `TypeError`, because tuples are immutable.
- The set `{3, 1, 3, 2}` kept only one `3`.
- We appended through `b`, and `a` changed too, because both names point at the same list. `a == c` is `True` (same values), but `a is c` is `False` (different objects).
- `s.upper()` returned a new string, and `s` itself is unchanged.

## Check yourself

1. What does this print? `x = [1]` then `y = x` then `y.append(2)` then `print(x)`.
   <details><summary>Answer</summary>

   `[1, 2]`. `y = x` doesn't copy the list. It makes `y` a second name for the same list object, so appending through `y` is visible through `x`.

   </details>
2. Why is `point[0] = 10` an error when `point = (3, 4)`, but fine when `point = [3, 4]`?
   <details><summary>Answer</summary>

   A tuple is immutable: once created it can never change. A list is mutable.

   </details>
3. When should you use `is` instead of `==`?
   <details><summary>Answer</summary>

   Almost only for `x is None` (or `x is not None`). `is` checks whether two names refer to the same object. `==` checks whether two values are equal, which is nearly always what you mean.

   </details>
4. Which Python types map directly to JSON?
   <details><summary>Answer</summary>

   `dict` (JSON object), `list` (array), `str` (string), `int`/`float` (number), `bool` (true/false) and `None` (null). Tuples are turned into lists, and sets aren't allowed at all.

   </details>
