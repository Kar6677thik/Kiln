# Enums (`Enum`, `IntEnum`, `StrEnum`)

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [classes and objects](classes-and-objects.md) · **Reading time:** ~9 min
> **Terms introduced:** **enum**, **member**, **magic string**, **StrEnum**, **IntEnum**

## What it looks like

```python
from enum import StrEnum


class Status(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


result = Status.FAIL
```

## What it means

An **enum** (short for *enumeration*) is a fixed, named list of the only values something is allowed to have. Here, a doctor check's status can be one of exactly three things, and nothing else.

Line by line:

- `class Status(StrEnum):` creates a new enum type called `Status`. It uses `class` syntax (see [classes and objects](classes-and-objects.md)), but enums behave specially.
- `PASS = "pass"` defines a **member** named `PASS` whose *value* is the string `"pass"`.
- `Status.FAIL` is how you refer to a member. Each member has a `.name` (`"FAIL"`) and a `.value` (`"fail"`).
- `Status("warn")` goes the other way: it looks a member up by its value. That's how text read from JSON or a config file becomes an enum member, and a value that isn't in the list raises `ValueError`.
- `list(Status)` gives all members, in the order they were defined.

The standard library's `enum` module offers three flavours that Kiln uses:

| Base class | Members behave like… | Comparing with a plain value | Use it when |
|---|---|---|---|
| `Enum` | only themselves | `Color.RED == 1` is `False` | the values are internal labels |
| **`StrEnum`** (3.11+) | strings | `Status.PASS == "pass"` is `True` | the values travel as text (JSON, logs, CLI output) |
| **`IntEnum`** | integers | `Level.HIGH > Level.LOW` works | the values are numbers with an order (for example exit codes) |

## Why people use it

The alternative is **magic strings**: writing `"fail"` as a bare string literal all over the code. Magic strings break silently. A typo such as `if status == "fial":` is valid Python; it never matches. With an enum, `Status.FIAL` fails **immediately** with `AttributeError`, and tools like mypy and your editor can catch it before the code even runs.

Enums also document the full list of possibilities in one place, so a reader can see every state at a glance. That makes them perfect for state machines like Kiln's job states (spec/03 §4) and worker states (spec/04 §1).

## How Kiln uses it

- [`src/kiln/doctor.py`](../../src/kiln/doctor.py) defines `class Status(StrEnum)` with `PASS`, `WARN`, `FAIL` and `INFO`. Because it's a `StrEnum`, `kiln doctor --json` can write the members straight into JSON as `"pass"`, `"warn"` and so on.
- From K1, `JobState` (`QUEUED`, `RUNNING`, `SUCCEEDED`, …) and `WorkerState` (`BOOTING`, `READY`, …) are enums. The code-style naming table says enum members are `UPPER_SNAKE` (spec/08 §6).

## Gotchas

- **Plain `Enum` members don't equal their values.** `Color.RED == 1` is `False`. Compare with `Color.RED`, or use `IntEnum`/`StrEnum` when equality with plain values is wanted.
- **Use `is` or `==` with members, not the raw string, in Kiln code.** `status is Status.FAIL` is the clearest. With a `StrEnum` the string comparison also works, but it brings the magic-string risk back.
- **`str()` of a member differs by flavour.** For `StrEnum`, `str(Status.FAIL)` and `f"{Status.FAIL}"` give `fail`. For a plain `Enum` they give `Color.RED`.
- **Looking up an unknown value raises `ValueError`**, as the traceback in "Try it" shows. Code that converts outside input must handle that.
- **Members are created once.** You can't make new members at runtime, and that's the point.

## Try it

```text
>>> from enum import Enum, IntEnum, StrEnum
>>> class Color(Enum):
...     RED = 1
...     GREEN = 2
... 
>>> Color.RED
<Color.RED: 1>
>>> Color.RED.name, Color.RED.value
('RED', 1)
>>> Color(2)
<Color.GREEN: 2>
>>> Color.RED == 1
False
>>> class Status(StrEnum):
...     PASS = "pass"
...     FAIL = "fail"
... 
>>> Status.PASS
<Status.PASS: 'pass'>
>>> Status.PASS == "pass"
True
>>> f"result: {Status.FAIL}"
'result: fail'
>>> list(Status)
[<Status.PASS: 'pass'>, <Status.FAIL: 'fail'>]
>>> Status("nope")
Traceback (most recent call last):
  File "<console>", line 1, in <module>
  File "/home/Megatron/.local/share/uv/python/cpython-3.15.1-linux-x86_64-gnu/lib/python3.15/enum.py", line 733, in __call__
    return cls.__new__(cls, value)
           ~~~~~~~~~~~^^^^^^^^^^^^
  File "/home/Megatron/.local/share/uv/python/cpython-3.15.1-linux-x86_64-gnu/lib/python3.15/enum.py", line 1222, in __new__
    raise ve_exc
ValueError: 'nope' is not a valid Status
>>> class Level(IntEnum):
...     LOW = 1
...     HIGH = 2
... 
>>> Level.HIGH > Level.LOW
True
```

The traceback is longer than usual because the error is raised *inside* the `enum` module's own code (the file paths point into the installed Python). The last line is the part that matters. The `~~~^^^` marks underline the exact expression that failed.

The typo demonstration ([`snippets/enum_1.py`](snippets/enum_1.py)):

```text
(wsl) $ python3.15 docs/python/snippets/enum_1.py
string typo equals 'fail'? False
enum typo: type object 'Status' has no attribute 'FIAL'
PASS  value='pass'  shown as OK
WARN  value='warn'  shown as !!
FAIL  value='fail'  shown as XX
INFO  value='info'  shown as --
from JSON text back to a member: warn True
```

The misspelled string compared silently as "not equal". The misspelled member stopped the program with a clear error.

## Check yourself

1. Why is `Status.FIAL` safer than `"fial"`?
   <details><summary>Answer</summary>

   `Status.FIAL` raises `AttributeError` immediately, and mypy can flag it before you run anything. The string `"fial"` is valid, so every comparison with it is silently false.

   </details>
2. You read `"warn"` from a JSON file. How do you turn it into a member, and what happens with `"oops"`?
   <details><summary>Answer</summary>

   `Status("warn")` returns `Status.WARN`. `Status("oops")` raises `ValueError: 'oops' is not a valid Status`.

   </details>
3. Why does Kiln's doctor use `StrEnum` rather than plain `Enum`?
   <details><summary>Answer</summary>

   Its statuses are written to JSON and printed as text. `StrEnum` members *are* strings, so they serialize and format as `"pass"` directly, while still being a closed, typo-proof set.

   </details>
