# Delivery Semantics: At-Most-Once, At-Least-Once, Exactly-Once

> **Milestone:** K1 · **Read before:** [state-machines](state-machines.md) · **Reading time:** ~10 min
> **Terms introduced:** **delivery semantics**, **at-most-once**, **at-least-once**, **exactly-once**, **side effect**, **idempotent**, **idempotency key**

## One-sentence summary

**Delivery semantics** answer "if things crash, how many times might my job's function run?" The honest answers are "at most once" or "at least once". "Exactly once" needs help from the task itself.

## Why you should care

Workers die: they get OOM-killed (research [§2.7](../research/python-mastery-research.md#27-celery-and-pre-fork-workers-memory-multiplied-by-concurrency-secondary-sources)), crash, or hang and get killed. When that happens mid-job, the master faces an impossible question. Did the function already send the email, charge the card, or write the file *before* the worker died? It can't know. So it must choose between two risks:
- not running the job again, which risks it never happening;
- running it again, which risks it happening twice.

Kiln makes that choice explicit ([ADR-010](../decisions.md#adr-010), [spec/03 §5](../spec/03-jobs-and-tasks.md#5-delivery-semantics-how-many-times-can-my-function-run)). It's also one of the most common systems interview questions.

## The mental model

**Analogy:** posting a letter with no tracking.
- Post it once and never again: it arrives **at most once** (maybe zero times if lost).
- Keep re-posting until you hear back: it arrives **at least once** (maybe twice if the reply got lost).
- **Exactly once** would need the recipient to recognise duplicates ("I already have letter #42") and throw the extras away.

**Where the analogy fails:** a duplicate letter is harmless. A duplicate *payment* isn't. And in a computer the "letter" (the job) and its "reply" (the result) travel through processes that can vanish at any instant.

```
 worker:   start ──── side effect happens ──── X crash ──── (result never sent)
 master:   sent "run" ......................... sees worker died: was the side effect done? UNKNOWN
             │
             ├── max_attempts = 1 (default) ──▶ LOST       → ran 0 or 1 times  (at-most-once)
             └── retry allowed              ──▶ run again  → ran 1, 2, … times (at-least-once)
```

## See it for yourself

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/delivery-semantics_1.py
```

```text
attempt 1: result lost
max_attempts=1: gave up
emails actually sent: ['a@example.com']
attempt 1: result lost
attempt 2: result lost
attempt 3: result lost
max_attempts=3: gave up
emails actually sent: ['a@example.com', 'a@example.com', 'a@example.com']
sent to a@example.com
already sent
```

- The fake `send_email` performs its **side effect** (appending to `emails_sent`) and *then* "crashes" before reporting success. That's exactly the dangerous window.
- With **one attempt**, the caller sees failure, yet the email *was* sent once. The caller can't tell.
- With **three attempts**, the same email was sent **three times**. Retrying turned a lost reply into duplicates.
- The last two lines show the fix: an **idempotent** version remembers each job id it has done, so a repeat does nothing.

## How it really works

- **At-most-once:** dispatch each job once and never retry after a crash. No duplicates, but a crash can lose the job. In Kiln it becomes `LOST`, with an error message saying why.
- **At-least-once:** retry until a success is *recorded*. Nothing is lost while attempts remain, but every crash after the side effect and before the report causes a duplicate.
- **Exactly-once** in the strict sense is impossible over unreliable processes and networks. The result report can always be lost after the effect happened. Systems that advertise it really provide at-least-once delivery *plus* deduplication: the task uses an **idempotency key**, typically the job id, and checks "did I already do job 42?" in the same durable store as the effect.
- **Idempotent** means doing it twice has the same result as doing it once. "Set the user's email to X" is idempotent. "Add €10 to the balance" isn't. Retries are only safe for idempotent work.

## How Kiln uses it

- Default `max_attempts = 1`: **at-most-once**. A worker death makes the job `LOST(worker_lost)` ([spec/03 §4.1](../spec/03-jobs-and-tasks.md#41-transition-table-every-allowed-transition-anything-else-is-a-bug-and-raises-assertionerror)).
- With `max_attempts > 1` and `worker_lost`/`timeout` in `retry_on` (per task or per submit): **at-least-once**, within the attempt budget. Use it only for idempotent tasks.
- [`master/jobs.py` → `JobTable.fail_attempt`](../../src/kiln/master/jobs.py) applies the rule: requeue (at the back) if the failure kind is in `retry_on` *and* attempts remain, otherwise go terminal.
- Each attempt is numbered. A late result for an old attempt number is ignored, so a retry and a slow first attempt can never both "win".

## Common misconceptions

- ❌ **Myth:** "retries make a system reliable."
  ✅ **Reality:** retries trade *lost* work for *duplicate* work. They're reliable only for idempotent tasks.
- ❌ **Myth:** "if I got an error back, the side effect didn't happen."
  ✅ **Reality:** the effect may have happened just before the crash or timeout. An error tells you the *report* failed, not the *action*.
- ❌ **Myth:** "exactly-once delivery is a setting you can turn on."
  ✅ **Reality:** it needs cooperation from the task (deduplication on a key). The queue alone can't provide it.

## Check yourself

1. Kiln's default is `max_attempts = 1`. A worker is OOM-killed mid-job. How many times did the function run?
   <details><summary>Answer</summary>

   Zero or one. It may have been killed before or after its side effect. The job is marked `LOST` and isn't run again: at-most-once.

   </details>
2. With `max_attempts = 3`, could a task's side effect happen twice? When?
   <details><summary>Answer</summary>

   Yes. If attempt 1 performs the effect and its worker then dies (or the job times out) before the result reaches the master, attempt 2 runs and performs it again.

   </details>
3. Which of these are idempotent: (a) "set status = shipped", (b) "append a log line", (c) "delete file X"?
   <details><summary>Answer</summary>

   (a) yes. (c) yes in effect: the file is gone either way, though the second delete may report "not found". (b) no: two appends give two lines.

   </details>
4. Predict: in the snippet, what would `max_attempts=2` print for "emails actually sent"?
   <details><summary>Answer</summary>

   `['a@example.com', 'a@example.com']`. Two attempts, each performing the side effect before "crashing".

   </details>

## Go deeper

- [spec/03 §5](../spec/03-jobs-and-tasks.md#5-delivery-semantics-how-many-times-can-my-function-run): Kiln's exact rules.
- Celery docs, "Should I use retry or acks_late?" (in the Tasks guide): the same trade-off in a production queue.
- Tyler Treat, "You Cannot Have Exactly-Once Delivery": a short, classic blog post on why.
