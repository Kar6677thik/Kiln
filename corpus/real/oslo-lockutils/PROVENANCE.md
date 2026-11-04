# Provenance: oslo-lockutils (required for SC-5)

- **Upstream:** OpenStack oslo.concurrency, `oslo_concurrency/tests/unit/test_lockutils.py` (7.2.0), `FileBasedLockingTestCase`.
- **Fixes found:**
  - Debian `python-oslo.concurrency` 7.2.0-4 carries a patch, "fix multiprocessing spawn failure in FileBasedLockingTestCase" (by Thomas Goirand). It moves the subprocess target to a module-level helper. See the [patch list](https://sources.debian.org/patches/python-oslo.concurrency/7.2.0-4). The patch page itself returned 404 on 2026-11-04; its description is known from a search-result summary.
  - Gentoo's [`0001-Fix-multiprocessing-tests-under-Python-3.14.patch`](https://ftp.fau.de/gentoo-portage/dev-python/oslo-concurrency/files/0001-Fix-multiprocessing-tests-under-Python-3.14.patch) (April 2026) instead pins the tests to `multiprocessing.get_context('fork')`. It returned 404 on 2026-11-03; its description comes from a search-result summary.
- **Retrieved:** 2026-11-04.
- **Gate G2.7:** ⚠️ partially reachable. The descriptions were reachable; the diffs weren't. The upstream source wasn't inspected.

## The upstream pattern (from the Debian patch description)

> "the test was previously defining both the target function and the locked function inside the test method, and under spawn/forkserver, local functions cannot be pickled, which caused a PicklingError."

## What the distilled version keeps and simplifies

| Kept | Simplified |
|---|---|
| A test *method* defines `locked` and `target` as **nested functions** | oslo's `lockutils.synchronized` decorator → a direct `fcntl.flock` |
| The nested `target` is passed as `multiprocessing.Process(target=target, ...)` | the assertion logic → printing the lines written |
| Under the `__main__` guard (so FA001 must not fire) | |

## Expected findings

- `FA002` at `distilled.py:47`, the `multiprocessing.Process(target=target, ...)` call. `target` is a nested function, so its qualname is `FileBasedLockingTestCase.test_lock_internally_between_processes.<locals>.target`.

## Observed behaviour (2026-11-04, CPython 3.15.1, K2 §5.9 bootstrap)

| method | outcome | output |
|---|---|---|
| fork | exit 0 | `lines written: ['child-0', 'child-1']` |
| forkserver | **exit 1** | `_pickle.PicklingError: Can't pickle local object <function FileBasedLockingTestCase.test_lock_internally_between_processes.<locals>.target at 0x…>`, raised in the parent at `child.start()` |
| spawn | **exit 1** | same PicklingError |
