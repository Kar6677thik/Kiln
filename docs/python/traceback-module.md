# The `traceback` module

> **First used in:** see the [ledger](README.md) (milestone K3) · **Needs:** [exceptions](exceptions.md), [functions](functions.md) · **Reading time:** ~5 min
> **Terms introduced:** **`traceback.extract_stack`**, **`traceback.format_list`**, **`FrameSummary`**

## What it looks like

```python
import traceback

frames = traceback.extract_stack()          # the call stack right now, oldest call first
caller = frames[-2]                          # [-1] is this line; [-2] is whoever called us
print(caller.filename, caller.lineno, caller.name)
print("".join(traceback.format_list(frames[-3:])))   # the familiar "File ..., line ..." text
```

## What it means

- **`traceback.extract_stack()`** returns a `StackSummary`, a list of **`FrameSummary`** objects, one per active function call, oldest first. Each has `filename`, `lineno`, `name` (the function) and `line` (the source text, when available). The last entry is the line that called `extract_stack` itself.
- **`traceback.format_list(frames)`** turns frames into the same text lines a traceback prints: `  File "x.py", line 3, in f`.
- **`traceback.format_exc()`**, inside an `except` block, returns the current exception's full traceback as a string, which is useful for logging.

## Why people use it

To answer "who called me?" without a debugger, and to record or send tracebacks as text (logs, error replies), instead of printing them to stderr.

## How Kiln uses it

- `src/kiln/memory/gcwatch.py`, layer A: the `gc.enable` shim calls `traceback.extract_stack()` and keeps the top frames, so the `gc_reenabled` event names the file and line that re-enabled the GC ([global-interpreter-state](../concepts/global-interpreter-state.md)).
- Workers already use its sibling `traceback.format_exception(error)` (K1, [`src/kiln/worker/executors/base.py`](../../src/kiln/worker/executors/base.py)) to turn a failed task's exception into text for the `result` message.

## Gotchas

- **`[-1]` is you.** The last frame is the `extract_stack()` call itself; the caller is `[-2]`.
- **Inside the REPL or `exec`,** filenames are pseudo-names like `<console>`, and source lines may be missing.
- **Stacks can be long.** Keep only what you need (Kiln keeps the top 8 frames) before logging.
- **`format_exc()` outside an `except` block** returns `'NoneType: None\n'`, not an error.

## Try it

```text
>>> import traceback
>>> def inner():
...     return traceback.extract_stack()
... 
>>> def outer():
...     return inner()
... 
>>> frames = outer()
>>> [(f.name, f.lineno) for f in frames[-3:]]
[('<module>', 1), ('outer', 2), ('inner', 2)]
>>> frames[-1].name, frames[-2].name
('inner', 'outer')
>>> print("".join(traceback.format_list(frames[-2:])), end="")
  File "<console>", line 2, in outer
  File "<console>", line 2, in inner
>>> try:
...     1 / 0
... except ZeroDivisionError:
...     text = traceback.format_exc()
... 
>>> text.splitlines()[-1]
'ZeroDivisionError: division by zero'
```

(Generated from [`snippets/traceback-module.repl`](snippets/traceback-module.repl) on Python 3.15.1.)

The stack reads oldest-first: the REPL's `<module>` called `outer`, which called `inner`, which called `extract_stack`.

## Check yourself

1. Inside a function `f`, which element of `traceback.extract_stack()` describes the function that called `f`?
   <details><summary>Answer</summary>

   `[-2]`. `[-1]` is the line in `f` that called `extract_stack`.

   </details>
2. How do you get an exception's traceback as a string to put into a log message?
   <details><summary>Answer</summary>

   Call `traceback.format_exc()` inside the `except` block.

   </details>
