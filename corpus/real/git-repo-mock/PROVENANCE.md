# Provenance: git-repo-mock (stretch case)

- **Upstream:** Google git-repo, `tests/test_subcmds_forall.py`. Fix commit [`fbc9c7919246ea74c295be888db4ae332cced202`](https://gerrit.googlesource.com/git-repo/+/fbc9c7919246ea74c295be888db4ae332cced202%5E%21/), "tests: Fix test compatibility with Python 3.14 forkserver".
- **Retrieved:** 2026-11-04.
- **Gate:** ✅ reachable (the commit page and diff summary).

## The upstream pattern (summarised from the commit)

- **Before:** the test used `mock.patch.object(project.Project, "GetRevisionId", return_value="refs/heads/main")` so that `forall` wouldn't query the remote.
- **After:** it sets `proj.revisionId = "refs/heads/main"` on each project instead, so the value travels with the Project objects that are passed to the workers.
- **The commit message's reasoning:** under forkserver, workers start from a fresh interpreter, so a class-level patch doesn't carry over.

## What the distilled version keeps and simplifies

| Kept | Simplified |
|---|---|
| A **class-level** `mock.patch.object` active in the parent | git-repo's `forall` machinery → a 2-worker `multiprocessing.Pool` mapping a module-level worker |
| The patched method is called on objects **pickled into** the workers | `GetRevisionId` running git → raising `RuntimeError("git not available …")` |

## Expected findings

- `FA006` at `distilled.py:58`, the `multiprocessing.Pool(2)` creation in the same `with` statement as the active `mock.patch.object`. Anchored on the process-creation line.

## Observed behaviour (2026-11-04, CPython 3.15.1, K2 §5.9 bootstrap)

| method | outcome | output |
|---|---|---|
| fork | exit 0 | `['platform/a=refs/heads/main', 'platform/b=refs/heads/main']`: the patch was inherited |
| forkserver | exit 0, **differs** | `['platform/a: git not available for platform/a', …]`: the workers see the unpatched class |
| spawn | exit 0, **differs** | same as forkserver |
