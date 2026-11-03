# Functions

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [variables and types](variables-and-types.md) · **Reading time:** ~12 min
> **Terms introduced:** **function**, **parameter**, **argument**, **return value**, **default value**, **keyword argument**, **`*args`**, **`**kwargs`**, **keyword-only parameter**, **mutable default**

## What it looks like

```python
def connect(host, port=7340, *, timeout_s=10.0):
    """Return a short description of where we'd connect."""
    return host + ":" + str(port) + " (timeout " + str(timeout_s) + "s)"


connect("127.0.0.1")  # '127.0.0.1:7340 (timeout 10.0s)'
connect("127.0.0.1", 9000, timeout_s=2)  # '127.0.0.1:9000 (timeout 2s)'
```

## What it means

A **function** is a named, reusable piece of code. You write it once with `def` and **call** it as many times as you like with `name(...)`.

Line by line:

- `def connect(...):` creates a function object and binds it to the name `connect`. Nothing inside runs yet.
- `host, port=7340, *, timeout_s=10.0` are the **parameters**, the names the function uses for its inputs.
  - `port=7340` gives a **default value**, used when the caller doesn't supply one.
  - The bare `*` means "everything after me must be passed *by name*". So `timeout_s` is a **keyword-only parameter**.
- The indented lines are the **body**. Python uses indentation (4 spaces) instead of braces to mark where the body starts and ends.
- The string on the first body line is the **docstring**, the function's built-in documentation. Kiln requires one on every function ([documentation §4.2](../standards/documentation.md#42-function-method-and-class-docstrings-every-function-including-private-ones-)).
- `return ...` ends the function and hands a **return value** back to the caller. A function with no `return` returns `None`.

When you *call* the function, the values you pass are **arguments**. You can pass them by position (`connect("127.0.0.1", 9000)`), or by name as **keyword arguments** (`connect(host="127.0.0.1", port=9000)`). Keyword arguments make calls easier to read and order-independent.

### Collecting extra arguments: `*args` and `**kwargs`

- A parameter written `*numbers` collects **any number of extra positional arguments** into a tuple. By convention it's called **`*args`**.
- A parameter written `**options` collects **any extra keyword arguments** into a dict. By convention it's called **`**kwargs`**.

Kiln's jobs carry exactly this pair: a list of positional `args` and a dict of `kwargs`, which the worker passes to your task as `func(*args, **kwargs)` ([spec/03](../spec/03-jobs-and-tasks.md)). In a *call*, `*` and `**` work in reverse: they *unpack* a list or dict into separate arguments.

## Why people use it

- **No repetition.** Write the logic once and call it from many places.
- **Names explain intent.** `classify_exit(status)` says what's happening. Twenty raw lines don't.
- **Testability.** A function with clear inputs and outputs can be tested on its own (see [pytest basics](pytest-basics.md)).
- **Keyword-only parameters** stop mistakes like `connect("host", 2)`, where the reader can't tell what `2` means.

## How Kiln uses it

- [`src/kiln/cli.py`](../../src/kiln/cli.py): `main()` takes the command-line arguments and *returns* an exit code (0 = success) instead of exiting itself. That's what makes it easy to test.
- [`src/kiln/doctor.py`](../../src/kiln/doctor.py): each environment check is a small function with the same shape: it takes a context and returns a result. The checks are kept in a list and called one after another.
- [`src/kiln/bench/records.py`](../../src/kiln/bench/records.py): `add_metric(record, name, unit, *, value=None, samples=None)` uses **keyword-only** parameters, so a call always says which of `value` or `samples` it's giving.

## Gotchas

- **The mutable-default trap.** A default value is created **once**, when `def` runs, not on every call. If the default is a mutable object like a list, every call shares that one list (see `remember` below). Use `None` as the default and create the list inside the function.
- **Forgetting `return`.** A function that `print`s a result but doesn't `return` it gives back `None`.
- **Parentheses matter.** `f` is the function object itself, and `f()` *calls* it. Writing `if check:` (without `()`) is always true, because function objects count as true.
- **Positional after keyword is an error.** `f(a=1, 2)` is a `SyntaxError`.

## Try it

```text
>>> def add(a, b):
...     return a + b
... 
>>> add(2, 3)
5
>>> add(b=10, a=1)
11
>>> def greet(name, greeting="Hello"):
...     return greeting + ", " + name + "!"
... 
>>> greet("Ada")
'Hello, Ada!'
>>> greet("Ada", greeting="Hi")
'Hi, Ada!'
>>> def shout(text):
...     print(text.upper())
... 
>>> result = shout("hi")
HI
>>> print(result)
None
>>> def total(*numbers):
...     return sum(numbers)
... 
>>> total(1, 2, 3)
6
>>> def describe(**options):
...     return options
... 
>>> describe(workers=4, labs=True)
{'workers': 4, 'labs': True}
>>> def connect(host, *, timeout_s=10):
...     return host + " (timeout " + str(timeout_s) + "s)"
... 
>>> connect("localhost", timeout_s=2)
'localhost (timeout 2s)'
>>> connect("localhost", 2)
Traceback (most recent call last):
  File "<console>", line 1, in <module>
TypeError: connect() takes 1 positional argument but 2 were given
>>> def remember(item, seen=[]):
...     seen.append(item)
...     return seen
... 
>>> remember("a")
['a']
>>> remember("b")
['a', 'b']
>>> def remember_fixed(item, seen=None):
...     if seen is None:
...         seen = []
...     seen.append(item)
...     return seen
... 
>>> remember_fixed("a")
['a']
>>> remember_fixed("b")
['b']
```

Things to notice:
- `shout` printed `HI` but *returned* `None`.
- `*numbers` arrived as a tuple, and `**options` as a dict.
- Passing `timeout_s` by position was refused, which is exactly the protection the `*` gives.
- `remember("b")` returned `['a', 'b']`. The default list survived from the first call. The fixed version creates a fresh list each time.

## Check yourself

1. What does `f()` return if `f` is `def f(): x = 1`?
   <details><summary>Answer</summary>

   `None`. The function has no `return` statement, so Python returns `None` automatically.

   </details>
2. Why is `def f(items=[])` dangerous, and what is the standard fix?
   <details><summary>Answer</summary>

   The list is created once, when `def` runs, and shared by every call that uses the default, so data leaks between calls. Use `items=None` and write `if items is None: items = []` inside the function.

   </details>
3. Given `def run(task, *args, **kwargs)`, what are `args` and `kwargs` in the call `run("add", 2, 3, retries=1)`?
   <details><summary>Answer</summary>

   `task` is `"add"`, `args` is the tuple `(2, 3)` and `kwargs` is the dict `{"retries": 1}`.

   </details>
4. What does the bare `*` in `def connect(host, *, timeout_s=10)` do?
   <details><summary>Answer</summary>

   Every parameter after it becomes keyword-only: you must write `timeout_s=2`. A plain positional `2` is rejected with a `TypeError`.

   </details>
