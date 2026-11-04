# bytearray and memoryview

> **First used in:** see the [ledger](README.md) (milestone K1) · **Needs:** [bytes-vs-str](bytes-vs-str.md), [slicing](slicing.md) · **Reading time:** ~6 min
> **Terms introduced:** **buffer**, **memoryview**, **copy (of bytes)**

## What it looks like

```python
buf = bytearray()          # an empty, growable buffer of bytes
buf += b"\x00\x00\x00\x05" # data arrives in pieces...
buf += b"hello"
del buf[:4]                # throw away the first 4 bytes we've already used
```

## What it means

- `bytes` is **immutable**: once made, it never changes. `data[0] = 120` raises `TypeError`.
- `bytearray` is the **mutable** twin. You can append to it (`+=`, `.extend()`), change single bytes, and delete ranges (`del buf[:4]`). A variable used to collect incoming data like this is called a **buffer**.
- `bytes(buf)` makes an immutable copy of a bytearray's current contents.
- A **`memoryview`** is a *window* onto existing bytes. Slicing a memoryview (`view[6:]`) gives another window, **without copying** the data. To get real `bytes`, call `bytes(view)` or `view.tobytes()`.

Indexing any of these gives a number from 0 to 255, not a one-character string: `b"abc"[0]` is `97`, the code for `a`.

## Why people use it

Data from a socket arrives in chunks of any size. A decoder needs a place to **accumulate** chunks until a complete message is there, then **remove** the part it consumed. With immutable `bytes`, every `+` would build a whole new object. A `bytearray` grows in place.

`memoryview` matters when data is large: slicing `bytes` copies the slice, slicing a memoryview doesn't.

## How Kiln uses it

- [`src/kiln/protocol/framing.py`](../../src/kiln/protocol/framing.py): `FrameDecoder` keeps a `bytearray`. `feed(data)` appends; each complete frame is cut out, and `del buf[:n]` drops the consumed bytes ([K1 spec §5.1](../milestones/K1-supervisor.md#51-kilnprotocolframing)).
- [`src/kiln/master/connections.py`](../../src/kiln/master/connections.py) keeps a `bytearray` *output* buffer per connection, and deletes what `send()` managed to write.

## Gotchas

- **`del buf[:n]` shifts the remaining bytes.** Python moves everything after position `n` to the front, so the cost grows with what's left in the buffer. For Kiln's frames (at most 1 MiB, usually a few hundred bytes) that's cheap. Servers handling huge streams use smarter tricks (a read offset, or a list of chunks).
- **Holding a memoryview keeps the bytearray locked in size.** While a memoryview of a bytearray exists, resizing it raises `BufferError`. Kiln's code avoids long-lived memoryviews for that reason.
- **Comparing bytes with text is always False.** `b"hi" == "hi"` is `False`. Decode or encode first.

## Try it

```text
>>> data = b"abc"
>>> data[0]
97
>>> data[0] = 120
Traceback (most recent call last):
  File "<console>", line 1, in <module>
TypeError: 'bytes' object does not support item assignment
>>> buf = bytearray()
>>> buf += b"\x00\x00"
>>> buf += b"\x00\x05hello"
>>> buf
bytearray(b'\x00\x00\x00\x05hello')
>>> len(buf)
9
>>> del buf[:4]
>>> buf
bytearray(b'hello')
>>> bytes(buf)
b'hello'
>>> view = memoryview(b"hello world")
>>> view[6:]
<memory at 0x7b24a228a800>
>>> bytes(view[6:])
b'world'
>>> view[6:].tobytes()
b'world'
```

(Generated from [`snippets/bytearray-and-memoryview.repl`](snippets/bytearray-and-memoryview.repl) on Python 3.15.1. The `0x...` address differs on every run.)

Notice how the frame arrived in two pieces (`b"\x00\x00"`, then the rest) and only made sense once both were in the buffer. That's exactly the problem framing solves.

## Check yourself

1. Why can't `FrameDecoder` store its buffer as `bytes`?
   <details><summary>Answer</summary>

   It could, but every new chunk would require building a brand-new `bytes` object (`buf = buf + data`), copying everything again. A `bytearray` grows and shrinks in place.

   </details>
2. After `buf = bytearray(b"abcdef")` and `del buf[:2]`, what is `buf`?
   <details><summary>Answer</summary>

   `bytearray(b'cdef')`. The first two bytes are removed, and the rest moves to the front.

   </details>
3. What does `memoryview(b"hello")[1:3]` give you, and how do you get `b"el"`?
   <details><summary>Answer</summary>

   A memoryview: a window onto bytes 1 and 2, without a copy. `bytes(...)` or `.tobytes()` turns it into `b'el'`.

   </details>
