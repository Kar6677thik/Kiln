# Clocks: `time.monotonic()` vs `time.time()` vs `time.perf_counter()`

> **First used in:** see the [ledger](README.md) (milestone K1) · **Needs:** [variables-and-types](variables-and-types.md) · **Reading time:** ~6 min
> **Terms introduced:** **wall-clock time**, **monotonic clock**, **perf_counter**, **deadline**, **Unix epoch**

## What it looks like

```python
import time

deadline = time.monotonic() + 30.0       # "30 seconds from now"
if time.monotonic() >= deadline:
    print("timed out")
```

## What it means

Python offers several clocks. Each returns a `float` number of seconds, but they mean different things:

| Function | Counts from | Can jump? | Use it for |
|---|---|---|---|
| `time.time()` | the **Unix epoch** (1 January 1970, 00:00 UTC) | **yes**: the system clock can be set forward or back (by you, by NTP time sync, after a laptop wakes) | **wall-clock time**: "when did this happen?", for humans and logs |
| `time.monotonic()` | an arbitrary starting point (often boot time) | **never goes backwards** | durations, timeouts and deadlines in a program |
| `time.perf_counter()` | an arbitrary starting point | never goes backwards | precise timing of short things (benchmarks), with the highest resolution |

A **monotonic clock** only moves forward, at a steady rate. Its absolute value means nothing (in the REPL below, `161.35` is roughly seconds since the WSL machine booted). Only **differences** between two readings matter.

A **deadline** is a moment on the monotonic clock by which something must happen: `deadline = time.monotonic() + timeout_s`. Checking it later is one subtraction.

## Why people use it

Imagine measuring a 30-second job timeout with `time.time()`, and the system clock gets corrected back by one hour in the middle. "Now minus start" suddenly becomes negative, so the job would get an extra hour before timing out. Or the clock jumps forward, and every running job "times out" at once. The monotonic clock is immune to both, which is why spec/01 §4 rule 5 says the master uses it for all its arithmetic.

`time.time()` is still the right choice for timestamps people read: "submitted at 14:03:07" has to be real-world time.

## How Kiln uses it

- [`src/kiln/master/timers.py`](../../src/kiln/master/timers.py) keeps every deadline (job timeouts, heartbeat checks, shutdown deadlines) as a `time.monotonic()` value. The arbiter passes `now = time.monotonic()` into it.
- [`src/kiln/master/jobs.py`](../../src/kiln/master/jobs.py) stores both kinds on each job: `submitted_mono` for calculations, and `submitted_wall_ms` (from `time.time()`) for the JobView that humans read. The naming rule (spec/08 §6) puts `_mono` or `_wall_ms` on every time variable so they never get mixed up.
- Benchmarks such as `bench/load.py` time things with `time.perf_counter()`.

## Gotchas

- **Never subtract a `time.time()` value from a `time.monotonic()` value.** They count from different starting points, so the result is meaningless. The `_mono` / `_wall_ms` suffixes exist to make that mistake visible.
- **Monotonic values aren't comparable between machines or reboots**, and generally not between processes either. Send wall-clock timestamps over the network, not monotonic ones.
- **`time.sleep(0.25)` sleeps *at least* 0.25 s.** It can be a little longer, because the OS wakes the thread when it gets around to it.

## Try it

```text
>>> import time
>>> time.time()
1793778272.6410728
>>> time.monotonic()
161.348975675
>>> time.perf_counter()
161.349017114
>>> start = time.monotonic()
>>> time.sleep(0.25)
>>> elapsed = time.monotonic() - start
>>> round(elapsed, 2)
0.25
>>> deadline = time.monotonic() + 30.0
>>> deadline - time.monotonic() > 29
True
>>> time.get_clock_info("time").adjustable
True
>>> time.get_clock_info("monotonic").adjustable
False
>>> time.get_clock_info("perf_counter").resolution
1e-09
```

(Generated from [`snippets/time-monotonic.repl`](snippets/time-monotonic.repl) on Python 3.15.1.)

- `time.time()` is about 1.79 billion seconds since 1970.
- `monotonic()` and `perf_counter()` are small numbers since this WSL machine started; on Linux they currently share the same underlying clock.
- `get_clock_info` confirms the key fact: the wall clock is `adjustable`, the monotonic clock is not.
- `perf_counter` claims nanosecond resolution (`1e-09`).

## Check yourself

1. A job started at `time.monotonic() == 100.0` with `timeout_s = 30`. At what monotonic value does it time out?
   <details><summary>Answer</summary>

   At `130.0`. The deadline is the start plus the timeout, and monotonic time can't jump, so this is exactly 30 seconds of real elapsed time later.

   </details>
2. Which clock should a log line's timestamp use, and which should a timeout use?
   <details><summary>Answer</summary>

   The log line uses `time.time()` (wall clock), because people need a real date and time. The timeout uses `time.monotonic()`, because it must not change when the system clock is adjusted.

   </details>
3. What does `time.monotonic()` returning `161.35` tell you?
   <details><summary>Answer</summary>

   Nothing on its own: the starting point is arbitrary. Only the difference between two monotonic readings has meaning.

   </details>
