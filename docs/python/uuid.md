# The `uuid` module (random ids)

> **First used in:** see the [ledger](README.md) (milestone K1) · **Needs:** [variables-and-types](variables-and-types.md) · **Reading time:** ~4 min
> **Terms introduced:** **UUID**, **uuid4**, **hex string**

## What it looks like

```python
import uuid

job_id = uuid.uuid4().hex      # e.g. '451ce58c77394ecd996687602ed472da'
```

## What it means

A **UUID** ("universally unique identifier") is a 128-bit number written in a standard text form like `451ce58c-7739-4ecd-9966-87602ed472da`.

- `uuid.uuid4()` makes a **version 4** UUID, which is 122 random bits (the other 6 bits mark the version and format). It returns a `UUID` object.
- `.hex` gives the same number as a **hex string**: 32 characters from `0-9a-f`, without the dashes. That's the format Kiln's job ids use (spec/02 §3.5).
- `uuid.UUID(text)` turns the text back into a `UUID` object.

## Why people use it

A server has to give each job a name that never repeats. A counter (1, 2, 3, ...) works only inside one process and restarts from 1 after a restart, so an old client could ask for job "17" and get someone else's job. A random 122-bit number needs no coordination at all. Collisions are so unlikely that systems treat uuid4s as unique: you would need to generate about 2.7 × 10¹⁸ of them before there is even a 50% chance that any two match.

They're also hard to guess, so one client can't trivially poke at another client's jobs by counting. That isn't real security (Kiln has none, ADR-021), but it's a nice side effect.

## How Kiln uses it

[`src/kiln/master/jobs.py`](../../src/kiln/master/jobs.py) creates every job id with `uuid.uuid4().hex` in `JobTable.submit`. The id is what `kiln submit` prints and what `kiln wait <id>` takes.

## Gotchas

- **Don't use `uuid1()` for ids you show to people.** It's built from the time and the machine's network address, which leaks information and is less random.
- **The `UUID` object and its `.hex` string are different types.** `job_id == job_id.hex` is `False`. Kiln stores and sends the string only, because JSON has no UUID type.
- **Random ids don't sort by creation time.** To list jobs in order, sort by `submitted_wall_ms`, not by id.

## Try it

```text
>>> import uuid
>>> job_id = uuid.uuid4()
>>> job_id
UUID('451ce58c-7739-4ecd-9966-87602ed472da')
>>> job_id.hex
'451ce58c77394ecd996687602ed472da'
>>> len(job_id.hex)
32
>>> uuid.uuid4().hex == uuid.uuid4().hex
False
>>> uuid.UUID(job_id.hex) == job_id
True
>>> job_id.version
4
```

(Generated from [`snippets/uuid.repl`](snippets/uuid.repl) on Python 3.15.1. Your ids will differ: they're random.)

## Check yourself

1. Why doesn't Kiln number its jobs 1, 2, 3...?
   <details><summary>Answer</summary>

   A counter restarts after the master restarts, so an old id could suddenly refer to a different job. Random uuid4 ids are unique without remembering anything, and they're hard to guess.

   </details>
2. How many characters is `uuid.uuid4().hex`, and which characters can appear?
   <details><summary>Answer</summary>

   32 characters, each a hex digit `0-9` or `a-f`. 128 bits ÷ 4 bits per hex digit = 32.

   </details>
3. Can you sort uuid4 job ids to find the oldest job?
   <details><summary>Answer</summary>

   No. They're random, so their order says nothing about time. Sort by the job's `submitted_wall_ms` instead.

   </details>
