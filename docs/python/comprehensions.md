# Comprehensions (list, dict, set) and generator expressions

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [variables and types](variables-and-types.md), [functions](functions.md) · **Reading time:** ~10 min
> **Terms introduced:** **comprehension**, **list comprehension**, **dict comprehension**, **set comprehension**, **generator expression**, **iterable**, **lazy evaluation**

## What it looks like

```python
nums = [1, 2, 3, 4, 5]
squares = [n * n for n in nums]  # [1, 4, 9, 16, 25]
evens = [n for n in nums if n % 2 == 0]  # [2, 4]
```

## What it means

A **comprehension** is a one-line recipe for building a new collection from an existing one. Read `[n * n for n in nums]` from the middle outwards:

1. `for n in nums`: go through `nums` one item at a time, calling the current item `n`.
2. `n * n`: for each `n`, compute this value.
3. `[ ... ]`: collect all the computed values into a new list.

An optional `if` at the end filters: `[n for n in nums if n % 2 == 0]` keeps only the items where the condition is true (`%` is the remainder after division, so `n % 2 == 0` means "even").

That one line does exactly the same as this loop:

```python
squares = []
for n in nums:
    squares.append(n * n)
```

The brackets decide what kind of collection you get:

| Written as | Name | Builds | Example |
|---|---|---|---|
| `[expr for x in items]` | **list comprehension** | a list | `[n * n for n in nums]` |
| `{key: value for x in items}` | **dict comprehension** | a dict | `{n: n * n for n in nums}` |
| `{expr for x in items}` | **set comprehension** | a set (no duplicates) | `{len(w) for w in words}` |
| `(expr for x in items)` | **generator expression** | a *generator* that produces values on demand | `sum(n * n for n in nums)` |

The thing after `in` can be anything you can loop over: a list, a dict (gives its keys), a string (gives its characters), a file (gives its lines), a range. In Python's vocabulary, something you can loop over is an **iterable**.

**Generator expressions are different.** They don't build a collection at all. They produce one value each time someone asks for the next one. This is called **lazy evaluation**. It saves memory when there are many items, and it's what makes `sum(...)`, `any(...)` and `all(...)` over a generator efficient. The catch: a generator can be consumed only **once**.

## Why people use it

- **Short and declarative:** "the list of ids that didn't pass" reads almost like the English sentence.
- **No half-built state:** the new list appears complete in one step, so there's no empty list being appended to in the meantime.
- **Faster** than an equivalent loop with `.append()` in CPython, though that rarely matters in Kiln.

## How Kiln uses it

- [`src/kiln/doctor.py`](../../src/kiln/doctor.py) uses comprehensions to pick out the failed and warning checks from the list of results, and `any(...)` with a generator expression to decide the exit code.
- [`scripts/check_docs.py`](../../scripts/check_docs.py) uses comprehensions to collect files and headings to check.

## Gotchas

- **When *not* to use one.** If the body needs several steps, temporary variables or a `try`, write a normal `for` loop. Kiln's code-style rule (§1) prefers an explicit loop whenever a comprehension would need nesting or a long condition. A comprehension should fit comfortably on one line.
- **Generators are single-use.** The second `sum(gen)` below returns `0`, because the generator was already used up.
- **Don't use a list comprehension only for its side effects.** `[print(x) for x in items]` builds a useless list of `None`s. Use a `for` loop.
- **Nested comprehensions** (`[x for row in grid for x in row]`) read in a surprising order: the outer loop comes first. Kiln avoids them.
- **The `{}` ambiguity.** `{}` alone is an empty *dict*, not an empty set. An empty set is `set()`.

## Try it

```text
>>> nums = [1, 2, 3, 4, 5]
>>> squares = []
>>> for n in nums:
...     squares.append(n * n)
... 
>>> squares
[1, 4, 9, 16, 25]
>>> [n * n for n in nums]
[1, 4, 9, 16, 25]
>>> [n for n in nums if n % 2 == 0]
[2, 4]
>>> {n: n * n for n in nums}
{1: 1, 2: 4, 3: 9, 4: 16, 5: 25}
>>> {len(word) for word in ["a", "bb", "cc", "ddd"]}
{1, 2, 3}
>>> gen = (n * n for n in nums)
>>> sum(gen)
55
>>> sum(gen)
0
```

Comprehensions over check results, like `kiln doctor` uses ([`snippets/comprehensions_1.py`](snippets/comprehensions_1.py)):

```text
(wsl) $ python3.15 docs/python/snippets/comprehensions_1.py
not passing: ['D4', 'D9']
lookup table: {'D1': 'pass', 'D4': 'warn', 'D9': 'warn', 'D14': 'pass'}
distinct statuses: ['pass', 'warn']
any failure? False
['d4: needs attention later', 'd9: needs attention later']
```

## Check yourself

1. Rewrite `[w.upper() for w in words if w]` as a `for` loop.
   <details><summary>Answer</summary>

   ```python
   result = []
   for w in words:
       if w:  # skip empty strings (an empty string counts as false)
           result.append(w.upper())
   ```

   </details>
2. What type is `{x for x in "hello"}`, and how many items does it have?
   <details><summary>Answer</summary>

   A set, with 4 items: `h`, `e`, `l`, `o`. Sets drop the duplicate `l`.

   </details>
3. Why did the second `sum(gen)` in "Try it" return 0?
   <details><summary>Answer</summary>

   `gen` is a generator expression. The first `sum` consumed every value, and a generator can't restart, so the second `sum` saw no values and returned the starting total, 0.

   </details>
