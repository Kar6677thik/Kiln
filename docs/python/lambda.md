# Lambda (small anonymous functions)

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [functions](functions.md) · **Reading time:** ~5 min
> **Terms introduced:** **lambda**, **anonymous function**, **key function**

## What it looks like

```python
double = lambda x: x * 2          # a tiny function, written as an expression
sorted(names, key=lambda name: len(name))
```

## What it means

`lambda x: x * 2` creates a function, exactly like `def` does, but in a single expression:

| Part | Meaning |
|---|---|
| `lambda` | "here comes a function" |
| `x` (before the colon) | its parameters, as in `def f(x):` |
| `x * 2` (after the colon) | **one expression** whose value is returned. There's no `return` keyword; the value is returned automatically |

So `double = lambda x: x * 2` does the same job as:

```python
def double(x):
    return x * 2
```

The only real differences:
- A lambda has no name of its own. It shows up as `<lambda>` in tracebacks, which is why it's called an **anonymous function**.
- Its body can only be one expression: no statements, no loops, no `if` blocks (a one-line `a if cond else b` expression is allowed).

## Why people use it

Some functions want *another function* as an argument. Writing a separate `def` for a one-line helper that is used exactly once is clutter. The classic case is a **key function** for sorting: `sorted(names, key=lambda name: len(name))` means "sort the names by their length", and the whole idea fits on one line.

## How Kiln uses it

Kiln's own code (`src/kiln`) doesn't use lambdas: [code-style §1](../standards/code-style.md#1-plain-python-preferred) prefers a named `def`, because a name and a docstring explain more. Lambdas appear only in **tests**, as tiny stand-ins:

- [`tests/unit/test_doctor.py`](../../tests/unit/test_doctor.py) builds a fake machine for `kiln doctor`. It passes `port_free=lambda port: True` ("every port is free") and `module_exists=lambda name: True` instead of the real functions.
- [`tests/snippets/test_snippets.py`](../../tests/snippets/test_snippets.py) uses `ids=lambda p: p.name`, so each snippet test is labelled with the snippet's file name.

## Gotchas

- **Don't assign a lambda to a name in real code.** `double = lambda x: x * 2` works, but `def double(x):` is clearer, gets a proper name in tracebacks (`double` instead of `<lambda>`), and can have a docstring. Ruff even warns about it (rule E731).
- **A lambda inside a loop captures the variable, not its value at that moment.** `[lambda: i for i in range(3)]` gives three functions that all return `2`, the last value of `i`. That's a closure detail, explained in K2's closures note.
- **Sometimes no lambda is needed.** `key=lambda name: len(name)` can be written `key=len`, because `len` is already a function taking one argument.

## Try it

```text
>>> double = lambda x: x * 2
>>> double(21)
42
>>> def double_def(x):
...     return x * 2
... 
>>> double_def(21)
42
>>> type(double)
<class 'function'>
>>> names = ["kiln", "doctor", "ab"]
>>> sorted(names, key=lambda name: len(name))
['ab', 'kiln', 'doctor']
>>> sorted(names, key=len)
['ab', 'kiln', 'doctor']
>>> which = lambda name: None if name == "cc" else f"/usr/bin/{name}"
>>> which("cc")
>>> which("gdb")
'/usr/bin/gdb'
>>> double.__name__
'<lambda>'
>>> double_def.__name__
'double_def'
```

(Generated from [`snippets/lambda.repl`](snippets/lambda.repl) with `scripts/repl_transcript.py` on Python 3.15.1.)

Notice that `which("cc")` printed nothing. It returned `None`, and the REPL doesn't print `None` results.

## Check yourself

1. Rewrite `square = lambda n: n * n` as a `def`.
   <details><summary>Answer</summary>

   ```python
   def square(n):
       return n * n
   ```
   The body of the lambda becomes the `return` value.

   </details>
2. Why can't you write `lambda x: return x`?
   <details><summary>Answer</summary>

   `return` is a *statement*, and a lambda's body must be a single *expression*. The expression's value is returned automatically, so the correct form is `lambda x: x`.

   </details>
3. What does `sorted(["bb", "a", "ccc"], key=lambda s: -len(s))` give?
   <details><summary>Answer</summary>

   `['ccc', 'bb', 'a']`. The key is the negative length (-3, -2, -1), and sorting from smallest to largest puts the longest string first.

   </details>
