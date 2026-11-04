# Amdahl's law

> **Milestone:** K4 · **Read before:** [cpu-vs-io-bound](cpu-vs-io-bound.md) · **Reading time:** ~8 min
> **Terms introduced:** **Amdahl's law**, **speed-up**, **serial fraction**, **parallel fraction**

## One-sentence summary

If a fraction *s* of a job can't be done in parallel, then no number of workers can make it more than *1/s* times faster: **Amdahl's law**.

## Why you should care

Every Kiln job passes through the master: it's received, queued, dispatched and its result handled, all on the master's single thread (ADR-006, ADR-007). K1's lab found the master's ceiling at roughly 10,000–12,000 trivial jobs per second ([K1 lab](../labs/K1-supervisor.md)). That serial part is why adding workers, threads or subinterpreters eventually stops helping, and Amdahl's law says how soon.

## The mental model

**Analogy:** a restaurant with one cashier and many cooks. Hiring more cooks speeds up cooking, but every order still goes through the one cashier. Once the cooks are fast enough, the queue at the cashier *is* the restaurant's speed.

**Where the analogy fails:** Amdahl's law assumes the serial part takes a fixed share of each job. In a real server the serial part can also *grow* with load (queues, lock contention), which makes things worse than the formula.

```
 one job, 1 worker:     [ serial s ][          parallel part (1 - s)           ]
 the same, N workers:   [ serial s ][ (1-s)/N ]
                                       ▲ shrinks as N grows
                         ▲ never shrinks: it sets the limit

 speed-up(N) = 1 / ( s + (1 - s) / N )        limit as N → ∞ :  1 / s
```

## See it for yourself

[`snippets/amdahls-law_1.py`](snippets/amdahls-law_1.py) evaluates the formula for a few serial fractions and worker counts, up to 14 (the CPU count of this machine):

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/amdahls-law_1.py
```

```text
serial  N=1    N=2    N=4    N=8    N=14   limit (N -> infinity)
   0%    1.00   2.00   4.00   8.00  14.00   inf
   5%    1.00   1.90   3.48   5.93   8.48   20.0
  10%    1.00   1.82   3.08   4.71   6.09   10.0
  25%    1.00   1.60   2.29   2.91   3.29   4.0
  50%    1.00   1.33   1.60   1.78   1.87   2.0
```

- With **no serial part**, the speed-up equals the number of workers.
- With just **10% serial**, 14 workers give 6.09×, not 14×, and no number of workers can ever exceed 10×.
- With **50% serial**, doubling the workers from 8 to 14 adds almost nothing (1.78× to 1.87×).

**Worked example.** Suppose a job takes 20 ms in total, of which 2 ms is serial work in the master (decoding, dispatching, handling the result) and 18 ms runs in a worker. Then *s* = 2/20 = 0.10. With 8 workers: 1 / (0.10 + 0.90/8) = 1 / 0.2125 = **4.71×**, the "10%, N=8" cell above. The limit is 1/0.10 = **10×**.

K4 lab numbers: see `docs/labs/K4-concurrency.md`.

## How it really works

- **Where the formula comes from.** With one worker, a job takes `T = s·T + (1-s)·T`. With N workers, only the parallel part is divided: `T_N = s·T + (1-s)·T/N`. The **speed-up** is `T / T_N`, and the `T`s cancel, giving `1 / (s + (1-s)/N)`. As N grows, `(1-s)/N` goes to 0, leaving `1/s`.
- **The serial fraction is about time, not code.** One line of code in a hot loop can be most of the serial fraction; thousands of lines run once at startup are not part of it.
- **What it ignores.** Amdahl's law assumes a fixed problem and free coordination. Real parallelism adds overhead (thread start-up, lock contention, cache traffic: 4 threads gave 3.4×, not 4×, in the [gil](gil.md) snippet), which makes the real curve fall *below* the formula. The opposite view, **Gustafson's law**, notes that with more workers people usually give them *more* work, so throughput can keep growing even when a single job's latency can't.

## How Kiln uses it

- The master's dispatch path is Kiln's serial fraction: every job goes through one thread there, by design (ADR-006, ADR-007).
- The K4 lab compares measured throughput at concurrency 1, 2, 4 and 8 with this formula, estimating *s* from the measurements.
- K8 attacks the serial fraction directly by making the master's per-job work cheaper (a native validator).

## Common misconceptions

- ❌ **Myth:** "twice the workers, twice the throughput."
  ✅ **Reality:** only if nothing is serial. With 10% serial, going from 8 to 14 workers improves 4.71× to 6.09×, not to 8.24×.
- ❌ **Myth:** "the serial part is small, so it doesn't matter."
  ✅ **Reality:** a 5% serial part caps the speed-up at 20×, however many cores you buy.

## Check yourself

1. If 10% of each job's time is spent in the serial master path, what's the maximum speed-up from adding workers?
   <details><summary>Answer</summary>

   1/0.10 = 10×, approached but never reached as the number of workers grows.

   </details>
2. Predict the speed-up with 4 workers when 25% of the work is serial.
   <details><summary>Answer</summary>

   1 / (0.25 + 0.75/4) = 1 / 0.4375 ≈ 2.29× (the table's "25%, N=4" cell).

   </details>
3. Measured throughput with 8 threads is lower than Amdahl's formula predicts for the serial fraction you estimated. Name two reasons.
   <details><summary>Answer</summary>

   Overheads the formula ignores: lock contention, thread start-up and scheduling, cache traffic between cores. Also, the serial fraction itself may grow with load, for example when the master's queue gets longer.

   </details>

## Go deeper

- Gene Amdahl, "Validity of the single processor approach to achieving large scale computing capabilities" (1967): the original three-page paper.
- John Gustafson, "Reevaluating Amdahl's law" (1988): the scaled-workload view.
- [K1 lab §8 (c)](../labs/K1-supervisor.md): the master's measured throughput ceiling.
