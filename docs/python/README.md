# Python Notes: Index and the Feature Ledger

> You're new to Python, so every language feature used anywhere in this repo gets a note here ([standards/documentation.md §3](../standards/documentation.md#3-python-notes-docspython)). K0 writes the **starter set** before any Kiln code exists. Later milestones add notes as new features appear.

## 1. Planned notes, in reading order

Status: ⏳ planned · ✍️ written · ✅ reviewed by the learner

| # | Note (`docs/python/<slug>.md`) | Milestone | Status |
|---|---|---|---|
| 1 | [`running-python-and-the-repl`](running-python-and-the-repl.md) | K0 | ✍️ |
| 2 | [`variables-and-types`](variables-and-types.md) | K0 | ✍️ |
| 3 | [`functions`](functions.md) | K0 | ✍️ |
| 4 | [`modules-and-imports`](modules-and-imports.md) | K0 | ✍️ |
| 5 | [`classes-and-objects`](classes-and-objects.md) | K0 | ✍️ |
| 6 | [`exceptions`](exceptions.md) | K0 | ✍️ |
| 7 | [`type-hints`](type-hints.md) | K0 | ✍️ |
| 8 | [`dataclasses`](dataclasses.md) | K0 | ✍️ |
| 9 | [`context-managers`](context-managers.md) | K0 | ✍️ |
| 10 | [`decorators`](decorators.md) | K0 | ✍️ |
| 11 | [`bytes-vs-str`](bytes-vs-str.md) | K0 | ✍️ |
| 12 | [`f-strings`](f-strings.md) | K0 | ✍️ |
| 13 | [`comprehensions`](comprehensions.md) | K0 | ✍️ |
| 14 | [`enum`](enum.md) | K0 | ✍️ |
| 15 | [`pathlib`](pathlib.md) | K0 | ✍️ |
| 16 | [`logging-basics`](logging-basics.md) | K0 | ✍️ |
| 17 | [`pytest-basics`](pytest-basics.md) | K0 | ✍️ |
| 18 | [`argparse`](argparse.md) | K0 | ✍️ |
| 18a | [`slicing`](slicing.md) | K0 | ✍️ |
| 18b | [`lambda`](lambda.md) | K0 | ✍️ |
| 19 | [`struct`](struct.md), [`json`](json.md), [`bytearray-and-memoryview`](bytearray-and-memoryview.md) | K1 | ✍️ |
| 20 | [`slots-and-frozen-dataclasses`](slots-and-frozen-dataclasses.md), [`collections-deque`](collections-deque.md), [`heapq`](heapq.md), [`typing-protocol`](typing-protocol.md) | K1 | ✍️ |
| 21 | [`threading-basics`](threading-basics.md), [`signal-module`](signal-module.md), [`os-module`](os-module.md), [`socket-module`](socket-module.md), [`selectors-module`](selectors-module.md) | K1 | ✍️ |
| 22 | [`uuid`](uuid.md), [`time-monotonic`](time-monotonic.md), [`hypothesis-basics`](hypothesis-basics.md), [`tomllib`](tomllib.md), [`importlib`](importlib.md), [`inspect`](inspect.md), [`asyncio-first-look`](asyncio-first-look.md) | K1 | ✍️ |
| 23 | [`if-name-main`](if-name-main.md), [`pickle-and-reduce`](pickle-and-reduce.md), [`runpy`](runpy.md), [`closures`](closures.md), [`functools-partial`](functools-partial.md) | K2 | ✍️ |
| 24 | [`ast-module`](ast-module.md), [`symtable-module`](symtable-module.md), [`multiprocessing-basics`](multiprocessing-basics.md) | K2 | ✍️ |
| 25 | [`gc-module`](gc-module.md), [`atexit`](atexit.md), [`sys-getrefcount`](sys-getrefcount.md), [`monkeypatching`](monkeypatching.md) | K3 | ✍️ |
| 26 | [`import-hooks-meta-path`](import-hooks-meta-path.md), [`ctypes-basics`](ctypes-basics.md), [`traceback-module`](traceback-module.md), [`tracemalloc-basics`](tracemalloc-basics.md) | K3 | ✍️ |
| 27 | [`threading`](threading.md), [`concurrent-futures`](concurrent-futures.md), [`async-await-basics`](async-await-basics.md), [`asyncio-run`](asyncio-run.md), [`locks`](locks.md) | K4 | ✍️ |
| 28 | `async-await-deep-dive`, `asyncio-streams`, `asyncio-taskgroup`, `asyncio-timeout` (+ `contextvars` if used) | K5 | ⏳ |
| 29 | `functools-lru-cache`, `weakref`, `del-and-finalizers` | K6 | ⏳ |
| 30 | `gc-callbacks`, `dynamic-classes-with-type`, `metaclass-basics` | K7 | ⏳ |
| 31 | `cprofile-and-profiling-tracing`, `timeit-and-perf-counter`, `importing-compiled-modules` | K8 | ⏳ |
| 32 | `t-strings`, `string-templatelib`, `statistics-module`, `random-with-seed`, `subprocess`, `lazy-imports` | K9 | ⏳ |

## 2. The ledger (filled in during implementation)

`scripts/check_docs.py` fails if a construct appears in the code without a row here.

| Feature | Note | First used in (file:line) | Milestone |
|---|---|---|---|
| `assert` | [pytest-basics](pytest-basics.md) | [`tests/snippets/test_snippets.py:100`](../../tests/snippets/test_snippets.py) | K0 |
| `async-await` | [asyncio-first-look](asyncio-first-look.md) | [`src/kiln/bench/load.py:118`](../../src/kiln/bench/load.py) | K1 |
| `classes-and-objects` | [classes-and-objects](classes-and-objects.md) | [`src/kiln/doctor.py:55`](../../src/kiln/doctor.py) | K0 |
| `closures` | [closures](closures.md) (first met in [decorators](decorators.md)) | [`src/kiln/app.py:127`](../../src/kiln/app.py) | K1 |
| `comprehensions` | [comprehensions](comprehensions.md) | [`src/kiln/bench/records.py:392`](../../src/kiln/bench/records.py) | K0 |
| `context-managers` | [context-managers](context-managers.md) | [`src/kiln/doctor.py:154`](../../src/kiln/doctor.py) | K0 |
| `dataclasses` | [dataclasses](dataclasses.md) | [`src/kiln/doctor.py:76`](../../src/kiln/doctor.py) | K0 |
| `decorators` | [decorators](decorators.md) | [`src/kiln/doctor.py:76`](../../src/kiln/doctor.py) | K0 |
| `del` | [bytearray-and-memoryview](bytearray-and-memoryview.md) (`del buf[:n]`; `del d[key]` removes a dict entry the same way) | [`src/kiln/protocol/framing.py`](../../src/kiln/protocol/framing.py) | K1 |
| `dunder-methods` | [classes-and-objects](classes-and-objects.md) (`__init__`), [dataclasses](dataclasses.md) (`__repr__`, `__eq__`) | [`src/kiln/errors.py`](../../src/kiln/errors.py) | K1 |
| `enum` | [enum](enum.md) | [`src/kiln/doctor.py:55`](../../src/kiln/doctor.py) | K0 |
| `exceptions` | [exceptions](exceptions.md) | [`src/kiln/bench/records.py:139`](../../src/kiln/bench/records.py) | K0 |
| `f-strings` | [f-strings](f-strings.md) | [`src/kiln/bench/records.py:69`](../../src/kiln/bench/records.py) | K0 |
| `functions` | [functions](functions.md) | [`src/kiln/bench/records.py:59`](../../src/kiln/bench/records.py) | K0 |
| `generators` | [context-managers](context-managers.md) (a function with `yield` pauses there; pytest fixtures use the same idea) | [`tests/support/kiln_helpers.py`](../../tests/support/kiln_helpers.py) | K1 |
| `global-and-nonlocal` | [closures](closures.md) (`nonlocal` in a nested function, `global` for a module variable) | [`src/kiln/labs/preload.py`](../../src/kiln/labs/preload.py) | K3 |
| `lambda` | [lambda](lambda.md) | [`tests/snippets/test_snippets.py:83`](../../tests/snippets/test_snippets.py) | K0 |
| `modules-and-imports` | [modules-and-imports](modules-and-imports.md) | [`src/kiln/__main__.py:20`](../../src/kiln/__main__.py) | K0 |
| `protocols` | [typing-protocol](typing-protocol.md) | [`src/kiln/worker/executors/base.py:31`](../../src/kiln/worker/executors/base.py) | K1 |
| `slicing` | [slicing](slicing.md) | [`scripts/check_docs.py:270`](../../scripts/check_docs.py) | K0 |
| `slots` | [slots-and-frozen-dataclasses](slots-and-frozen-dataclasses.md) | [`src/kiln/app.py:40`](../../src/kiln/app.py) | K1 |
| `star-args-and-unpacking` | [functions](functions.md) | [`tests/unit/test_doctor.py:63`](../../tests/unit/test_doctor.py) | K0 |
| `type-hints` | [type-hints](type-hints.md) | [`src/kiln/bench/records.py:59`](../../src/kiln/bench/records.py) | K0 |

The "first used in" line numbers are a snapshot from the end of K0. `check_docs.py` (DOC005) checks that every construct has a row; it doesn't check the line numbers.
