# Framing: Finding Message Boundaries in a Byte Stream

> **Milestone:** K1 · **Read before:** [sockets-and-socketpairs](sockets-and-socketpairs.md), [bytes-vs-str](../python/bytes-vs-str.md) · **Reading time:** ~12 min
> **Terms introduced:** **byte stream**, **frame**, **length prefix**, **big-endian**, **partial read**, **decoder buffer**

## One-sentence summary

TCP and stream sockets deliver a continuous **byte stream** with no gaps between messages, so Kiln puts a 4-byte **length prefix** in front of each message and the receiver uses it to cut the stream back into whole messages (**frames**).

## Why you should care

The most common networking bug is assuming "one send = one receive". It works in quick tests on an idle machine and then fails under load, when messages arrive glued together or split in half. Every Kiln message (client requests, worker results, heartbeats) travels as a KP/1 frame ([spec/02 §2](../spec/02-protocol.md#2-kp1-frame-format)). If the decoder is wrong, everything is wrong. That's why K1 tests it by feeding bytes one at a time and splitting at every possible position.

## The mental model

**Analogy:** a ticker tape with no spaces. Someone sends you `{"n":1}` and then `{"n":2}`, but the tape arrives as one strip, `{"n":1}{"n":2}`, sometimes cut in odd places: `{"n":` … `1}{"n":2}`. The fix is to agree that every message starts with its length written in a fixed-size box. You read the box, then read exactly that many characters, then the next box.

**Where the analogy fails:** a tape is read by a human who can guess. A program can't guess. If the length box itself arrives split (2 of its 4 bytes now, 2 later), the decoder has to wait for the rest before it knows anything.

```
 what was sent:     [len=7]{"n":1}   [len=7]{"n":2}

 the byte stream:   00 00 00 07 7b 22 6e 22 3a 31 7d 00 00 00 07 7b 22 6e 22 3a 32 7d
                    └─header──┘ └────payload 7 bytes──┘ └─header──┘ └────payload────┘
 recv() #1 gets:    00 00 00 07 7b 22 6e                ← partial: hold on to it
 recv() #2 gets:                         22 3a 31 7d 00 00 00 07 7b 22 6e 22 3a 32 7d
                                         → completes frame 1, AND contains all of frame 2
```

## See it for yourself

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/framing_1.py
```

```text
one recv() got: b'{"n":1}{"n":2}'
payload is 16 bytes; header bytes = [0, 0, 0, 16]
fed 20 single bytes; a message came out only at byte [20]
decoded: [{'type': 'ping'}]
two frames glued together: [{'type': 'ping'}, {'type': 'ping'}]
```

1. Two separate `sendall` calls were read by **one** `recv`, glued together. Nothing marks where the first message ends.
2. `struct.pack("!I", 16)` produced the header `[0, 0, 0, 16]`: 4 bytes, most significant first. That's **big-endian**, also called network byte order (see [struct](../python/struct.md)).
3. The teaching decoder was fed the 20-byte frame **one byte at a time**. For 19 calls it returned nothing (a **partial read**: not enough bytes yet). On byte 20 it returned the whole message, exactly once.
4. Two frames in one chunk came out as two messages.

## How it really works

The decoder keeps a **decoder buffer** (a `bytearray`) of bytes received but not yet used. Each time data arrives:

1. Append the new bytes to the buffer.
2. If the buffer holds fewer than 4 bytes, stop and wait for more.
3. Read the length N from the first 4 bytes (`struct.unpack("!I", ...)`).
   - If N > the maximum allowed (1 MiB, `protocol.max_frame_bytes`), fail **now**. Don't wait for a gigabyte that a buggy or malicious peer claims is coming ([spec/02 §2.1](../spec/02-protocol.md#21-what-a-receiver-does-on-bad-input)).
   - If N is 0, that's also invalid.
4. If the buffer holds fewer than 4 + N bytes, stop and wait.
5. Cut out the N payload bytes, delete the 4 + N bytes from the front of the buffer, decode UTF-8 → JSON, and check that it's an object.
6. Go back to step 2: the buffer may contain more complete frames.

**Why a length prefix and not a newline after each message?** Newline framing means scanning every byte for `\n` and trusting that payloads never contain one. A fixed header says up front exactly how much to read, and it lets the receiver reject oversized frames before reading them ([ADR-008](../decisions.md#adr-008)). HTTP/2, PostgreSQL and Kafka frame their messages the same way.

**The cost of `del buf[:n]`.** Deleting from the front of a `bytearray` moves the remaining bytes down in memory. With small frames and a buffer that rarely holds more than a few frames, that's cheap. A design for huge streams would track an offset instead. The K1 tour discusses this trade-off.

The sender must also cope with **partial writes**: the kernel may accept only part of a frame. The master keeps an output buffer per connection and sends the rest when the socket is writable again.

## How Kiln uses it

- [`protocol/framing.py` → `FrameDecoder`](../../src/kiln/protocol/framing.py) is the production version of the teaching decoder above, with strict errors (`FrameTooLarge`, `BadFrame`) and NaN rejection. `encode_frame` builds frames, and refuses to send one the peer would reject.
- Its tests feed frames one byte at a time, concatenate several, and use Hypothesis to split at every possible offset ([spec/02 §2.2](../spec/02-protocol.md#22-the-reference-decoder-a-behaviour-spec-not-code)).
- Every connection in [`master/connections.py`](../../src/kiln/master/connections.py) and the worker channel in [`worker/main.py`](../../src/kiln/worker/main.py) owns one decoder.

## Common misconceptions

- ❌ **Myth:** "On localhost, messages arrive exactly as sent."
  ✅ **Reality:** the snippet's two sends arrived as one chunk on a local socketpair. Splits happen too, especially with big messages or full buffers.
- ❌ **Myth:** "If `recv(4096)` returned fewer than 4096 bytes, the message is complete."
  ✅ **Reality:** `recv` returns whatever had arrived at that moment. Only the length prefix tells you whether the frame is complete.
- ❌ **Myth:** "The header can be read in one go because it's only 4 bytes."
  ✅ **Reality:** even the header can be split across two `recv` calls. The decoder must handle "2 of 4 header bytes so far".

## Check yourself

1. Why does a TCP `recv(4096)` sometimes return half a message? How does `FrameDecoder` cope?
   <details><summary>Answer</summary>

   TCP is a byte stream: the kernel hands over whatever bytes have arrived, with no idea where messages begin or end. The decoder keeps the leftover bytes in its buffer and only returns a message once it has the 4-byte header plus all N payload bytes.

   </details>
2. Predict the 4 header bytes for a 300-byte payload.
   <details><summary>Answer</summary>

   `[0, 0, 1, 44]`. 300 = 1 × 256 + 44, written big-endian (most significant byte first) as an unsigned 32-bit number.

   </details>
3. Why does the decoder reject an oversized length *as soon as the header arrives*, instead of after reading the body?
   <details><summary>Answer</summary>

   Waiting would mean buffering up to 4 GiB that the peer claims to be sending. Checking the header first rejects it in constant memory, and the master then closes the connection, because it can't skip the bytes safely.

   </details>

## Go deeper

- [spec/02 §1–2](../spec/02-protocol.md#1-why-framing-is-needed-read-this-if-tcp-is-new): Kiln's exact frame rules.
- [Python docs: `struct`](https://docs.python.org/3/library/struct.html), format characters and byte order.
- RFC 9113 §4 (HTTP/2 frame format): a real-world length-prefixed protocol.
