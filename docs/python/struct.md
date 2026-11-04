# struct (numbers ↔ bytes)

> **First used in:** see the [ledger](README.md) (milestone K1) · **Needs:** [bytes-vs-str](bytes-vs-str.md), [slicing](slicing.md) · **Reading time:** ~7 min
> **Terms introduced:** **struct**, **format string**, **byte order**, **big-endian**, **little-endian**

## What it looks like

```python
import struct

HEADER = struct.Struct("!I")       # "a 4-byte unsigned integer, in network byte order"
header = HEADER.pack(5)            # the number 5 → b'\x00\x00\x00\x05'
(length,) = HEADER.unpack(header)  # b'\x00\x00\x00\x05' → (5,)
```

## What it means

A socket carries **bytes**, but a message's length is a **number**. The standard-library `struct` module converts between the two, following a **format string** that describes the bytes:

| Piece of `"!I"` | Meaning |
|---|---|
| `!` | **byte order** = "network order", which is **big-endian**: the most significant byte comes first |
| `I` | an unsigned integer stored in 4 bytes, so 0 to 4,294,967,295 |

- `struct.pack("!I", 258)` gives the 4 bytes `00 00 01 02`, because 258 = 1 × 256 + 2.
- `struct.unpack("!I", data)` does the reverse. It always returns a **tuple**, even for one value. That's why the code writes `(length,) = ...`, which unpacks a one-item tuple into one variable.
- `struct.Struct("!I")` compiles the format once and gives you an object with `.pack`, `.unpack` and `.size` (4). It's slightly faster, and it's easier to read when you reuse the same format everywhere.

**Big-endian vs little-endian.** The same number can be laid out in two orders. `<I` is **little-endian**: least significant byte first, which is how x86 CPUs store numbers in memory. `struct.pack("<I", 258)` gives `02 01 00 00`. Both sides of a connection must agree on one order. Network protocols traditionally use big-endian, which is why it's called "network order".

## Why people use it

Almost every binary protocol (HTTP/2, PostgreSQL, Redis, Kafka, and Kiln's KP/1) starts its messages with fixed-size numbers. Without `struct` you'd do the arithmetic by hand (`n // 256 ** 3`, ...). The standard library also has `int.to_bytes(4, "big")` and `int.from_bytes(...)` for single integers, but `struct` handles a whole record of mixed fields in one call.

## How Kiln uses it

- [`src/kiln/protocol/framing.py`](../../src/kiln/protocol/framing.py) defines `HEADER = struct.Struct("!I")`. Every KP/1 frame is `HEADER.pack(len(payload)) + payload`, and the decoder reads the first 4 buffered bytes with `HEADER.unpack_from(...)` to learn how long the frame is ([spec/02 §2](../spec/02-protocol.md#2-kp1-frame-format)).

## Gotchas

- **Out-of-range numbers raise `struct.error`.** `"I"` is unsigned, so `-1` doesn't fit, and neither does `2**32`. Kiln checks the frame size against `max_frame_bytes` before packing anyway.
- **`unpack` needs exactly the right number of bytes.** Two bytes for a 4-byte format raises `unpack requires a buffer of 4 bytes`. A decoder must wait until 4 bytes have arrived. Half a header is normal on a TCP stream (see `docs/concepts/framing.md` in K1).
- **Forgetting the byte-order prefix.** Without `!`, `<` or `>`, struct uses the *machine's native* order *and* native alignment padding. Always write the prefix for anything sent over a network.

## Try it

```text
>>> import struct
>>> struct.pack("!I", 5)
b'\x00\x00\x00\x05'
>>> struct.pack("!I", 258)
b'\x00\x00\x01\x02'
>>> struct.pack("<I", 258)
b'\x02\x01\x00\x00'
>>> struct.unpack("!I", b"\x00\x00\x01\x02")
(258,)
>>> HEADER = struct.Struct("!I")
>>> HEADER.size
4
>>> frame = HEADER.pack(5) + b"hello"
>>> frame
b'\x00\x00\x00\x05hello'
>>> (length,) = HEADER.unpack(frame[:4])
>>> length
5
>>> frame[4:4 + length]
b'hello'
>>> struct.pack("!I", -1)
Traceback (most recent call last):
  File "<console>", line 1, in <module>
struct.error: 'I' format requires 0 <= number <= 4294967295
>>> struct.pack("!I", 2**32)
Traceback (most recent call last):
  File "<console>", line 1, in <module>
struct.error: 'I' format requires 0 <= number <= 4294967295
>>> struct.unpack("!I", b"\x00\x01")
Traceback (most recent call last):
  File "<console>", line 1, in <module>
struct.error: unpack requires a buffer of 4 bytes
```

(Generated from [`snippets/struct.repl`](snippets/struct.repl) with `scripts/repl_transcript.py` on Python 3.15.1.)

The `frame` lines are a whole KP/1 frame in miniature: 4 header bytes saying "5", then the 5 payload bytes.

## Check yourself

1. What does `struct.pack("!I", 1)` return?
   <details><summary>Answer</summary>

   `b'\x00\x00\x00\x01'`: four bytes, with the value in the last (least significant) byte, because `!` means big-endian.

   </details>
2. Why does `HEADER.unpack(b"\x00\x00\x00\x05")` return `(5,)` and not `5`?
   <details><summary>Answer</summary>

   `unpack` always returns a tuple, one item per field in the format string. Formats like `"!IH"` contain several fields; a one-field format still returns a one-item tuple.

   </details>
3. A sender uses `"<I"` and a receiver `"!I"`. The sender sends the length 1. What length does the receiver read?
   <details><summary>Answer</summary>

   16,777,216. The bytes are `01 00 00 00`; read big-endian, the `01` is the most significant byte, so the value is 1 × 256³. Both sides must agree on byte order.

   </details>
