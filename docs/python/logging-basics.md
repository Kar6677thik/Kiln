# Logging basics

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [functions](functions.md), [modules and imports](modules-and-imports.md), [f-strings](f-strings.md) · **Reading time:** ~12 min
> **Terms introduced:** **logging**, **logger**, **log level**, **handler**, **formatter**, **basicConfig**, **stderr**

## What it looks like

```python
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kiln.demo")

log.info("worker started")
log.warning("queue is %d%% full", 90)
```

## What it means

**Logging** means a program writing a running diary of what it's doing, so humans and tools can find out later what happened. The standard library's `logging` module has four parts:

```
log.warning("...")                    one log call
      │
      ▼
  LOGGER  "kiln.demo"     ── is WARNING ≥ this logger's level? ── no ──▶ dropped
      │ yes
      ▼
  HANDLER(s)              ── where it goes: terminal, file, ... (each handler can also filter by level)
      │
      ▼
  FORMATTER               ── what the line looks like: "WARNING kiln.demo: queue is 90% full"
```

- A **logger** is a named source of log messages. You get one with `logging.getLogger("name")`. Names are dotted like module names (`kiln.master`), and loggers form a family tree: `kiln.master` is a child of `kiln`. The usual convention, and Kiln's rule, is one logger per module: `log = logging.getLogger(__name__)`, where `__name__` is the module's own dotted name.
- A **log level** is a severity tag. From least to most severe: `DEBUG` (10), `INFO` (20), `WARNING` (30), `ERROR` (40), `CRITICAL` (50). A logger or handler set to a level drops everything below it.
- A **handler** decides *where* a message goes: the terminal, a file, the network. One logger can have several handlers.
- A **formatter** decides *what the line looks like*, using placeholders such as `%(levelname)s`, `%(name)s` and `%(message)s`.
- **`logging.basicConfig(...)`** is a one-call setup for simple programs. It attaches one handler (writing to **stderr**) with the format and level you give. **stderr** ("standard error") is a second output stream every program has, next to normal output (stdout). The [files and file descriptors](../concepts/files-and-file-descriptors.md) primer explains both.

Values are passed **separately**: `log.warning("queue is %d%% full", 90)`. The logging module fills them into the message only *if* the message is actually going to be shown. (`%d` means "an integer goes here", and `%%` is a literal percent sign.)

`log.exception("...")` is used inside an `except` block. It logs at `ERROR` and appends the full traceback.

Without any setup, Python uses a minimal fallback that prints `WARNING` and above, as bare messages, to stderr. That's why the "Try it" session below shows the warning but not the info line.

## Why people use it

Why not `print()`?

| Need | `print()` | `logging` |
|---|---|---|
| Show only important messages in production, everything when debugging | edit the code | change one level setting |
| Send messages to a file *and* the terminal, in different formats | write it yourself | add a second handler |
| Know which part of the program said something | write it into every message | the logger name is attached automatically |
| Severity, timestamps, process id on every line | manual | one format string |
| Tracebacks attached to error reports | manual | `log.exception(...)` |

For a server like Kiln, which runs for days with many processes, logs are often the **only** record of what went wrong. Research §2.7's Back Market story is a case in point: their only clue to OOM kills was a vague "worker lost" message.

## How Kiln uses it

- K0 doesn't log yet. [`src/kiln/doctor.py`](../../src/kiln/doctor.py) and [`src/kiln/cli.py`](../../src/kiln/cli.py) print *results* for a human, which is output, not a diary.
- From K1, every Kiln process logs **events**: one line per thing that happened, with a stable event name and `key=value` fields, for example `level=ERROR role=master event=worker_died worker_id=2 signal=SIGKILL suspected_oom=true`. The full catalogue is in [spec/06](../spec/06-observability.md). Tests and labs search the logs for these event names, which is why they never change once chosen.
- The setup code lives in `kiln/logsetup.py` (K1), with a helper `event(log, level, name, **fields)`. Code-style §5 forbids `print` in `src/kiln` (ruff rule `T20`) and requires one logger per module.

## Gotchas

- **`basicConfig` only works the first time.** If anything already configured logging, later calls silently do nothing.
- **Two levels filter a message:** the logger's own level, then each handler's level. In [`snippets/logging_basics_2.py`](snippets/logging_basics_2.py), DEBUG reached one handler but not the other.
- **Don't pre-build log messages with f-strings** on busy code paths: `log.debug(f"got {big_object}")` builds the string even when DEBUG is off. Pass values as arguments instead (code-style §5).
- **Duplicate lines** usually mean a message went to the logger's own handler *and* up the family tree to the root logger's handler. `log.propagate = False` stops the upward trip.
- **Logs go to stderr by default**, not stdout. If you redirect a program's output with `>`, its logs still appear on screen unless you also redirect stderr (`2>&1`).
- **Logging and `fork()` don't mix naively.** A child process inherits the parent's handlers and their locks. spec/06 §1.4 and the K1 `fork-and-threads` primer explain how Kiln deals with this.

## Try it

With no configuration at all, only WARNING and above appear, as bare text:

```text
>>> import logging
>>> log = logging.getLogger("kiln.demo")
>>> log.info("worker started")
>>> log.warning("queue is almost full")
queue is almost full
>>> log.getEffectiveLevel(), logging.WARNING
(30, 30)
```

With `basicConfig` ([`snippets/logging_basics_1.py`](snippets/logging_basics_1.py)). Logs go to stderr, so `2>&1` merges them into what you see:

```text
(wsl) $ python3.15 docs/python/snippets/logging_basics_1.py 2>&1
INFO kiln.demo: worker started
WARNING kiln.demo: queue is 90% full
ERROR kiln.demo: worker 3 died
ERROR kiln.quiet: shown: ERROR passes the logger's level
ERROR kiln.demo: division failed
Traceback (most recent call last):
  File "/mnt/c/Users/Karthik Reddy/Documents/Study/Projects/Kiln/docs/python/snippets/logging_basics_1.py", line 18, in <module>
    1 / 0
    ~~^~~
ZeroDivisionError: division by zero
```

The DEBUG line and the filtered WARNING never appear. `log.exception` appended the traceback, and the program still exited normally, because the exception was caught.

One logger, two handlers, two formats ([`snippets/logging_basics_2.py`](snippets/logging_basics_2.py)). The second format previews Kiln's `key=value` style:

```text
(wsl) $ python3.15 docs/python/snippets/logging_basics_2.py
level=DEBUG logger=kiln.master event=worker_spawned
[INFO] worker_ready
level=INFO logger=kiln.master event=worker_ready
```

## Check yourself

1. A logger is set to `INFO`. Which of `debug`, `info`, `warning` and `error` calls produce output?
   <details><summary>Answer</summary>

   `info`, `warning` and `error`. `DEBUG` (10) is below `INFO` (20), so it's dropped.

   </details>
2. What is the difference between a logger and a handler?
   <details><summary>Answer</summary>

   A logger is the named *source* that your code calls. A handler is a *destination* attached to it (the terminal, a file, …). One logger can feed several handlers, each with its own format and level.

   </details>
3. Why does Kiln write `log.debug("got %s", item)` rather than `log.debug(f"got {item}")`?
   <details><summary>Answer</summary>

   With separate arguments, the message text is only built if DEBUG is enabled. The f-string version does the formatting work on every call, even when the line is thrown away.

   </details>
4. Why does Kiln forbid `print` inside `src/kiln`?
   <details><summary>Answer</summary>

   `print` output has no level, no source and no structure, and it can't be filtered or redirected per destination. Kiln's logs must be machine-searchable events (spec/06). CLI *results* for humans go through one helper in `cli.py` instead.

   </details>
