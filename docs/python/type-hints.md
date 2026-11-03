# Type Hints

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [variables and types](variables-and-types.md), [functions](functions.md) · **Reading time:** ~11 min
> **Terms introduced:** **type hint (annotation)**, **return annotation**, **`X | None`**, **generic type (`list[int]`)**, **type checker**, **mypy**, **`--strict`**

## What it looks like

```python
def describe(port: int) -> str:
    return "listening on port " + str(port)


timeout_s: float = 2.5
names: list[str] = ["a", "b"]
pidfile: str | None = None
```

## What it means

A **type hint** (also called an **annotation**) is a note in the code saying what type a value is *expected* to have.

- `port: int` says "the parameter `port` should be an int".
- `-> str` after the parentheses is the **return annotation**: "this function returns a str".
- `timeout_s: float = 2.5` annotates a variable.
- `list[str]` means "a list whose items are strings". A type written with square brackets like this is a **generic type**: the outer type is a container, and the part in brackets says what it contains. Similarly, `dict[str, int]` maps str keys to int values.
- **`X | None`** means "an X, or `None`". It's the standard way to say "this might be missing". `str | None` reads "a string, or nothing".

### The crucial fact: Python doesn't check hints when running

Type hints are **not** enforced at run time. Python stores them (you can see them in `__annotations__`), but it doesn't stop you from passing the wrong type. In "Try it", `double("ab")` happily returns `'abab'` even though the hint says `int`.

So who reads them? A **type checker**: a separate program that reads your code *without running it* and reports places where the types don't fit. Kiln uses **mypy** ([ADR-019](../decisions.md#adr-019)), in **`--strict`** mode, which requires a hint on every function and refuses most loopholes. `./scripts/dev.sh typecheck` runs it, and `./scripts/dev.sh check` runs it before every commit.

```
 your code ──▶ python ──▶ runs it (hints ignored)
     │
     └──────▶ mypy ────▶ "line 10: expected int, got str"   (nothing is run)
```

## Why people use it

- **Mistakes caught before running.** mypy finds `describe("7340")` without running anything. In a server that runs for days, finding a bug at review time beats finding it at 3 a.m.
- **Documentation that can't go stale.** `def classify_exit(handle: WorkerHandle, wait_status: int) -> Death` tells you what goes in and what comes out. And if the hint is wrong, mypy complains.
- **Better editor help.** Editors use hints for autocomplete and to underline mistakes as you type.

## How Kiln uses it

- **Everything in `src/kiln` is fully annotated** and must pass `mypy --strict` ([code-style §3](../standards/code-style.md#3-typing)). Open any K0 file, for example [`src/kiln/doctor.py`](../../src/kiln/doctor.py) or [`src/kiln/bench/records.py`](../../src/kiln/bench/records.py), and every parameter and return value has a hint.
- [dataclasses](dataclasses.md) *need* hints: `check_id: str` is what makes `check_id` a field.
- Kiln uses the modern spellings: `list[str]` rather than `List[str]`, and `str | None` rather than `Optional[str]`. Older code you read elsewhere may use the old ones, and they mean the same thing.

## Gotchas

- **A wrong hint is worse than none.** If the hint says `int` but you pass a str, Python won't complain; only the checker can, and only if you run it.
- **`None` needs to be in the hint.** `def find(name: str) -> int:` that sometimes returns `None` is a lie. Write `-> int | None`, and mypy then forces callers to handle the `None` case.
- **Hints don't convert.** `port: int = "7340"` doesn't turn the string into an int.
- **Annotations are evaluated lazily from Python 3.14** (PEP 649). That's why a hint can mention a class defined later in the file. You rarely need to care, but it explains why older code writes hints in quotes, like `"WorkerHandle"`.

## Try it

```text
>>> def double(n: int) -> int:
...     return n * 2
... 
>>> double(4)
8
>>> double("ab")
'abab'
>>> double.__annotations__
{'n': <class 'int'>, 'return': <class 'int'>}
>>> timeout_s: float = 2.5
>>> names: list[str] = ["a", "b"]
>>> def find(name: str) -> int | None:
...     return None
... 
>>> print(find("x"))
None
```

`double("ab")` ran without complaint, because the hint didn't stop it. `"ab" * 2` is string repetition, so the result is `'abab'`.

Now the same kind of mistake in a file. [`type-hints_1.py`](snippets/type-hints_1.py) passes a string where an int is expected:

```text
(wsl) $ python3.15 docs/python/snippets/type-hints_1.py
listening on port 7340
listening on port 7340
```

Python ran both calls happily. A type checker catches the mistake. [`type-hints_2.py`](snippets/type-hints_2.py) contains it without the `# type: ignore` comment that silences mypy:

```text
(wsl) $ uvx --python 3.15 mypy docs/python/snippets/type-hints_2.py
docs/python/snippets/type-hints_2.py:10: error: Argument 1 to "describe" has incompatible type "str"; expected "int"  [arg-type]
Found 1 error in 1 file (checked 1 source file)
```

(`uvx` runs a tool without installing it into the project. Inside Kiln you'll use `./scripts/dev.sh typecheck`.) The `[arg-type]` at the end is the **error code**. Kiln's rule is that a `# type: ignore` must always name its code and give a `# WHY:` ([code-style §3](../standards/code-style.md#3-typing)).

## Check yourself

1. `def f(x: int) -> int: return x + 1`. What happens if you call `f(1.5)`?
   <details><summary>Answer</summary>

   It returns `2.5`. Python doesn't check hints at run time. mypy, reading the call, would report that a float was passed where an int was expected.

   </details>
2. What does `-> str | None` mean?
   <details><summary>Answer</summary>

   The function returns either a string or `None`. Callers must handle the `None` case, and mypy enforces that.

   </details>
3. If Python ignores hints, why does Kiln require them everywhere?
   <details><summary>Answer</summary>

   mypy uses them to find type mistakes before the code runs, and they document every function's inputs and outputs in a form that's checked, so it can't drift from the code.

   </details>
