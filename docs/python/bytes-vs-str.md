# Bytes vs. str (text vs. raw data)

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [variables and types](variables-and-types.md) · **Reading time:** ~12 min
> **Terms introduced:** **str**, **bytes**, **encoding**, **decoding**, **UTF-8**, **bytearray**, **byte**

## What it looks like

```python
s = "héllo"  # str: text, a sequence of characters
b = s.encode("utf-8")  # bytes: b'h\xc3\xa9llo', a sequence of numbers 0-255
back = b.decode("utf-8")  # str again: 'héllo'
```

## What it means

Python has two different types that look a bit alike but mean very different things:

| Type | Literal | Holds | Example item |
|---|---|---|---|
| **`str`** | `"héllo"` | **text**: a sequence of Unicode *characters* (letters, digits, emoji…) | `s[0]` is `'h'` |
| **`bytes`** | `b"hello"` | **raw data**: a sequence of **bytes**, where each **byte** is a number from 0 to 255 | `b[0]` is `104` |

Computers store and send *only bytes*. Files on disk, network packets and pipes between processes all carry numbers 0–255. Text is an idea that humans care about. To store or send text you need rules for turning characters into bytes and back:

- **Encoding** turns `str` into `bytes`: `"héllo".encode("utf-8")`.
- **Decoding** turns `bytes` into `str`: `b"h\xc3\xa9llo".decode("utf-8")`.

**UTF-8** is the most common set of rules (it's an *encoding*), and it's the one Kiln uses everywhere. In UTF-8, plain English letters take 1 byte each, `é` takes 2 bytes, and `☕` takes 3. That's why `len("héllo")` is 5 (characters) but `len("héllo".encode("utf-8"))` is 6 (bytes).

How Python *shows* bytes: `b'h\xc3\xa9llo'`. The `b` prefix means "this is bytes". Bytes that happen to match a readable ASCII character are shown as that character (`h`, `l`, `o`). The others are shown as `\x` followed by two hex digits (`\xc3` is the number 195).

A **`bytearray`** is the *changeable* (mutable) cousin of `bytes`. You can append to it with `+=` and remove from the front with `del buf[:n]`, a bit like a list of numbers. It's the natural tool for collecting data that arrives in pieces.

## Why people use it

Keeping text and bytes separate stops a whole family of bugs where text gets garbled ("mojibake": `cafÃ©` instead of `café`). Python 3 refuses to mix them silently: `"abc" + b"abc"` is an error. You have to decide *where* text becomes bytes, which is usually at the edge of your program:

```
  your program (str)  ──encode──▶  file / socket / pipe (bytes)  ──decode──▶  other program (str)
```

The rule of thumb: **decode as early as possible when bytes come in, and encode as late as possible when text goes out.**

## How Kiln uses it

- [`src/kiln/bench/records.py`](../../src/kiln/bench/records.py) turns each experiment record into JSON text, then writes it as **UTF-8** bytes to disk.
- From K1, every message between the master, workers and clients travels over **sockets**, which carry only bytes. Kiln's protocol (spec/02 §2) encodes JSON text as UTF-8 and puts a 4-byte length in front. The receiver collects incoming bytes in a `bytearray` until a whole message has arrived, then decodes it. The K1 note on `struct` and the `framing` primer continue from here.
- Reading files: Kiln always passes `encoding="utf-8"` explicitly (see [pathlib](pathlib.md)), so the result never depends on the computer's settings.

## Gotchas

- **`str` and `bytes` never compare equal.** `"abc" == b"abc"` is `False`, with no error. Your `if` silently takes the wrong branch.
- **Mixing them in `+` raises `TypeError`.** That's a good thing: it tells you a decode or encode is missing.
- **Indexing differs.** `"abc"[0]` is the string `'a'`, but `b"abc"[0]` is the *number* `97`.
- **Not every byte sequence is valid UTF-8.** `b"\xff".decode("utf-8")` raises `UnicodeDecodeError`. Data from the network can be garbage, so code that decodes it must be ready for this.
- **Decoding with the wrong encoding may *not* fail.** Latin-1 accepts any byte, so UTF-8 data decoded as Latin-1 gives garbage text without an error (see the snippet below).
- **`len()` means different things.** For `str` it counts characters; for `bytes` it counts bytes. A protocol's length prefix must count *bytes*.

## Try it

```text
>>> s = "héllo"
>>> len(s)
5
>>> b = s.encode("utf-8")
>>> b
b'h\xc3\xa9llo'
>>> len(b)
6
>>> b.decode("utf-8")
'héllo'
>>> type(s), type(b)
(<class 'str'>, <class 'bytes'>)
>>> s[0]
'h'
>>> b[0]
104
>>> "abc" == b"abc"
False
>>> "abc" + b"abc"
Traceback (most recent call last):
  File "<console>", line 1, in <module>
TypeError: can only concatenate str (not "bytes") to str
>>> b"\xff".decode("utf-8")
Traceback (most recent call last):
  File "<console>", line 1, in <module>
UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte
>>> buf = bytearray()
>>> buf += b"abc"
>>> buf += b"def"
>>> buf
bytearray(b'abcdef')
>>> bytes(buf[:4])
b'abcd'
>>> len("😀"), len("😀".encode("utf-8"))
(1, 4)
```

The snippet [`snippets/bytes_vs_str_1.py`](snippets/bytes_vs_str_1.py) shows a wrong-encoding decode, and a `bytearray` collecting two JSON messages that arrive in pieces, then cutting the first one off the front:

```text
(wsl) $ python3.15 docs/python/snippets/bytes_vs_str_1.py
characters: 12
bytes:      16
raw bytes:  b'na\xc3\xafve caf\xc3\xa9 \xe2\x98\x95'
first 6 bytes as numbers 0-255: [110, 97, 195, 175, 118, 101]
round trip ok: True
decoded as latin-1: naÃ¯ve cafÃ© â
buffer has 31 bytes: b'{"type":"ping"}{"type":"stats"}'
took: b'{"type":"ping"}' | left: b'{"type":"stats"}'
```

Look at the `latin-1` line: no error, only garbage. (The `☕` became `â` followed by two invisible control characters.)

## Check yourself

1. How many bytes is `"héllo".encode("utf-8")`, and why isn't it 5?
   <details><summary>Answer</summary>

   6. UTF-8 needs two bytes (`\xc3\xa9`) for `é`. The other four letters are one byte each.

   </details>
2. What does `b"hi"[0]` evaluate to?
   <details><summary>Answer</summary>

   The integer `104`, the byte value of `h`. Indexing `bytes` gives numbers, not one-character strings.

   </details>
3. Why does a network message's length prefix have to count bytes, not characters?
   <details><summary>Answer</summary>

   The receiver gets raw bytes and must know how many to read before it can decode anything. Character counts and byte counts differ as soon as non-ASCII text (like `é`) appears.

   </details>
4. You read data from a socket and compare it with `if data == "ping":`. It never matches. Why?
   <details><summary>Answer</summary>

   `data` is `bytes` and `"ping"` is `str`, and the two types are never equal. Compare with `b"ping"`, or decode first: `data.decode("utf-8") == "ping"`.

   </details>
