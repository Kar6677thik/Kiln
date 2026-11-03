# f-strings (formatted string literals)

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [variables and types](variables-and-types.md) · **Reading time:** ~8 min
> **Terms introduced:** **f-string**, **replacement field**, **format spec**, **repr**

## What it looks like

```python
name = "Ada"
jobs = 3
message = f"{name} ran {jobs} jobs"  # 'Ada ran 3 jobs'
```

## What it means

An **f-string** is a string literal with the letter `f` in front of the opening quote. Inside it, anything between curly braces `{ }` is a Python expression. Python evaluates it and inserts the result, converted to text. Each `{...}` is called a **replacement field**.

- `f"{name} ran {jobs} jobs"` evaluates `name` (gives `Ada`) and `jobs` (gives `3`), and builds the string `'Ada ran 3 jobs'`.
- Any expression works inside the braces: `f"{jobs * 2}"`, `f"{len(items)}"`, `f"{user.name}"`.
- Without the `f`, braces are plain characters: `"{name}"` stays `'{name}'`.

You can add extras after the expression:

| Syntax | Meaning | Example → result |
|---|---|---|
| `{x:SPEC}` | a **format spec**: how to lay the value out | `f"{3.14159:.2f}"` → `'3.14'` |
| `{x!r}` | use the value's **repr**: the "developer view" Python shows in the REPL, with quotes around strings | `f"{'Ada'!r}"` → `"'Ada'"` |
| `{x=}` | debugging shortcut: prints the expression text, `=`, then the value | `f"{jobs=}"` → `'jobs=3'` |

Common format specs:

| Spec | Meaning | Example |
|---|---|---|
| `.2f` | 2 digits after the decimal point | `f"{2/3:.2f}"` → `'0.67'` |
| `,` | thousands separator | `f"{1234567:,}"` → `'1,234,567'` |
| `>8` / `<8` | right/left-align in a field 8 characters wide | `f"[{'Ada':>8}]"` → `'[     Ada]'` |
| `.1%` | multiply by 100, show as a percentage | `f"{0.256:.1%}"` → `'25.6%'` |

## Why people use it

Before f-strings, you built text by gluing pieces together: `name + " ran " + str(jobs) + " jobs"`. That's easy to get wrong (forgetting `str()` raises a `TypeError`) and hard to read. f-strings put each value exactly where it appears in the sentence, and the format spec handles alignment and rounding.

## How Kiln uses it

- [`src/kiln/doctor.py`](../../src/kiln/doctor.py) uses f-strings with alignment specs to print its check table (check id, status, detail).
- [`src/kiln/cli.py`](../../src/kiln/cli.py) builds the `kiln --version` line with an f-string.
- Error messages everywhere follow the "what happened / why / what to do" rule (documentation standard §4.6), usually built with f-strings that include the offending value with `!r`, so empty strings and spaces are visible.
- **One deliberate exception:** log messages. Kiln's logging calls pass values as *separate arguments* instead of pre-building an f-string, so the text is only built if that log level is actually enabled (code-style §5). See [logging basics](logging-basics.md).

## Gotchas

- **Forgetting the `f`.** `"{name} ran"` prints the braces literally. No error, only wrong output.
- **Literal braces** need doubling: `f"{{not a field}}"` → `'{not a field}'`.
- **Quotes inside braces.** Before Python 3.12, you couldn't reuse the same quote character inside the braces (`f"{d["key"]}"` was a syntax error). Kiln's floor is 3.13, so it works, but you'll see older code written as `f"{d['key']}"`.
- **f-strings are evaluated immediately.** The values are looked up the moment the line runs. If you need a template you fill in *later*, you need something else. Python 3.14's **t-strings** (`t"..."`, K9) produce a template object instead of a finished string, which is also how they help prevent injection.
- **Never build SQL or shell commands with f-strings from untrusted input.** That's the injection problem that K9's t-string primer explains.

## Try it

```text
>>> name = "Ada"
>>> jobs = 3
>>> f"{name} ran {jobs} jobs"
'Ada ran 3 jobs'
>>> f"{jobs * 2} next time"
'6 next time'
>>> f"{name!r}"
"'Ada'"
>>> f"{jobs=}"
'jobs=3'
>>> pi = 3.14159265
>>> f"{pi:.2f}"
'3.14'
>>> f"{1234567:,}"
'1,234,567'
>>> f"[{name:>8}]"
'[     Ada]'
>>> f"[{name:<8}]"
'[Ada     ]'
>>> f"{0.256:.1%}"
'25.6%'
>>> "not an f-string: {name}"
'not an f-string: {name}'
```

A doctor-style table built with alignment specs ([`snippets/f_strings_1.py`](snippets/f_strings_1.py)):

```text
(wsl) $ python3.15 docs/python/snippets/f_strings_1.py
check status  detail
D1    pass    running on Linux
D9    warn    no C compiler (needed by K4)
D16   pass    scripts use LF line endings
2/3 passed (67%)
```

## Check yourself

1. What does `f"{2 + 3}"` produce? And `"{2 + 3}"`?
   <details><summary>Answer</summary>

   `'5'`: the f-string evaluates the expression. The plain string has no `f`, so it stays `'{2 + 3}'`, braces included.

   </details>
2. `x = ""`. Why might an error message use `f"bad value {x!r}"` rather than `f"bad value {x}"`?
   <details><summary>Answer</summary>

   With `!r` the message shows `bad value ''`, so you can *see* the value is an empty string. Without it, the message ends in a space and the problem is invisible.

   </details>
3. Predict `f"{1/3:.3f}"`.
   <details><summary>Answer</summary>

   `'0.333'`: three digits after the decimal point, rounded.

   </details>
