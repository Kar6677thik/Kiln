# Slicing

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [variables-and-types](variables-and-types.md) · **Reading time:** ~6 min
> **Terms introduced:** **slicing**, **index**, **IndexError**

## What it looks like

```python
argv = ["kiln", "doctor", "--json"]
first = argv[0]  # one item, by position:      "kiln"
rest = argv[1:]  # a slice: items 1 to the end: ["doctor", "--json"]
```

## What it means

Lists, strings, tuples and `bytes` are **sequences**: their items sit in a fixed order, numbered from **0**. The number of an item is its **index**.

- `argv[0]` asks for the item at index 0, the first one. This is called **indexing**, and it gives back a single item.
- `argv[1:]` is **slicing**. It gives back a *new* sequence of the same kind (a list from a list, a string from a string) containing a range of items.

A slice is written `[start:stop]`:

| Part | Meaning | If you leave it out |
|---|---|---|
| `start` | the first index to include | start from the beginning (0) |
| `stop` | the first index to **exclude** | go to the end |
| `:step` (optional third part) | take every step-th item | step 1, so every item |

The rule people trip on: **`stop` is not included.** `letters[1:3]` gives the items at index 1 and 2, but not 3. One way to remember it is that the slice holds `stop - start` items.

**Negative numbers count from the end.** `-1` is the last item, `-2` the one before it. So `letters[-2:]` means "the last two items".

## Why people use it

Programs constantly need "part of" a sequence:
- "everything except the program name" (`sys.argv[1:]`)
- "the first 4 bytes of a message" (the length prefix of a Kiln frame, in K1)
- "the last 10 lines"

Without slicing you'd write a loop that copies items one by one. A slice says the same thing in a few characters, and Python does the copying for you.

## How Kiln uses it

- [`scripts/repl_transcript.py`](../../scripts/repl_transcript.py) and [`scripts/check_docs.py`](../../scripts/check_docs.py) pass `sys.argv[1:]` (every command-line argument after the script's own name) to their `main()` function.
- `scripts/check_docs.py` uses `statements[1:]` to skip a function's docstring and look only at the code after it.
- From K1 on, the protocol code ([spec/02 §2](../spec/02-protocol.md#2-kp1-frame-format)) slices incoming `bytes`. It reads `data[:4]` for the 4-byte length and the following bytes for the message.

## Gotchas

- **The `stop` index is excluded.** `word[:5]` of `"framing"` is `'frami'` (5 letters), not `'framin'`.
- **Slices never raise for out-of-range numbers, but indexing does.** `letters[10:]` quietly gives `[]`, while `letters[10]` raises `IndexError`. That's convenient, but it can hide bugs: a wrong slice gives you an empty result instead of an error.
- **A slice is a copy.** `copy = letters[:]` makes a new list with the same items, so changing `copy` doesn't change `letters`. Compare that with `y = x`, which only adds a second name for the *same* list (see [variables-and-types](variables-and-types.md)). The copy is **shallow**: if the items are themselves lists, both lists share those inner lists.

## Try it

```text
>>> letters = ["a", "b", "c", "d", "e"]
>>> letters[0]
'a'
>>> letters[1:3]
['b', 'c']
>>> letters[:2]
['a', 'b']
>>> letters[2:]
['c', 'd', 'e']
>>> letters[-1]
'e'
>>> letters[-2:]
['d', 'e']
>>> letters[::2]
['a', 'c', 'e']
>>> letters[10:]
[]
>>> letters[10]
Traceback (most recent call last):
  File "<console>", line 1, in <module>
IndexError: list index out of range
>>> argv = ["kiln", "doctor", "--json"]
>>> argv[1:]
['doctor', '--json']
>>> word = "framing"
>>> word[:5]
'frami'
>>> data = b"\x00\x00\x00\x05hello"
>>> data[:4]
b'\x00\x00\x00\x05'
>>> data[4:]
b'hello'
>>> copy = letters[:]
>>> copy is letters
False
>>> copy == letters
True
```

(Generated from [`snippets/slicing.repl`](snippets/slicing.repl) with `scripts/repl_transcript.py` on Python 3.15.1. The real REPL shows `<python-input-N>` where this shows `<console>`.)

The `data` lines preview K1: a Kiln message starts with 4 bytes giving its length (here 5), followed by the message itself (`hello`). Slicing splits the two.

## Check yourself

1. What does `"kiln.toml"[:-5]` give?
   <details><summary>Answer</summary>

   `'kiln'`. `-5` means "5 from the end", so the slice stops before the last 5 characters (`.toml`) and keeps everything before them.

   </details>
2. `nums = [10, 20, 30]`. What do `nums[3:]` and `nums[3]` do?
   <details><summary>Answer</summary>

   `nums[3:]` gives `[]`: a slice starting past the end is empty, with no error. `nums[3]` raises `IndexError`, because there is no item at index 3 (the indexes are 0, 1 and 2).

   </details>
3. After `b = a[:]` and then `b.append(4)`, has `a` changed?
   <details><summary>Answer</summary>

   No. `a[:]` built a new list, so `b` is a separate object. `b.append(4)` changes only `b`. (With `b = a` instead, both names would point at the same list, and `a` would change too.)

   </details>
