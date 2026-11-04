# Provenance: fsl-pyfeeds (required for SC-5)

- **Upstream:** FSL pyfeeds, [merge request 46](https://git.fmrib.ox.ac.uk/fsl/pyfeeds/-/merge_requests/46).
- **Retrieved:** attempted 2026-11-03 and 2026-11-04. Both times the connection was refused (`ECONNREFUSED 163.1.84.35:443`).
- **Gate G2.8:** ❌ unreachable. **Source not inspected.** This case is **reconstructed** from the symptom recorded in the research notes (§00: "logging handlers relied on fork") and errata E-07.

## What the reconstruction assumes

pyfeeds runs feed tests in child processes. Its logging is configured in the parent's `main()`, and child code logs through module loggers. Under fork the children inherit the parent's handlers. Under forkserver/spawn they start without any, so INFO-level records disappear. (The "last resort" handler only prints WARNING and above, unformatted.)

| Kept (from the symptom) | Invented for the reconstruction |
|---|---|
| `logging.basicConfig(...)` only inside `main()`, which runs under the `__main__` guard | test names (`fslmaths`, `bet`) and messages |
| child targets log at INFO through `logging.getLogger(__name__)` | one `Process` per test, started and joined in turn |
| no child-side logging setup, and no Pool initializer | |

If MR 46 becomes reachable, compare it with this file, and update this note and `expected.json` if the real pattern differs.

## Expected findings

- `FA003` at `distilled.py:32`, the `multiprocessing.Process(target=run_test, ...)` call: its target logs, but logging is only configured in the parent. Anchored on the process-creation line.

## Observed behaviour (2026-11-04, CPython 3.15.1, K2 §5.9 bootstrap)

| method | outcome | output |
|---|---|---|
| fork | exit 0 | 6 lines: `PYFEEDS INFO starting 2 tests`, then for each test `running test …` and `test … passed`, then `all tests finished` |
| forkserver | exit 0, **differs** | only `PYFEEDS INFO starting 2 tests` and `PYFEEDS INFO all tests finished`: the 4 child lines are silently lost |
| spawn | exit 0, **differs** | same as forkserver |
