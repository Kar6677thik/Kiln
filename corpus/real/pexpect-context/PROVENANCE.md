# Provenance: pexpect-context (stretch case)

- **Upstream:** downstream packaging patches for pexpect 4.9.0 under Python 3.14. The openSUSE `py314.patch` and redcore's `pexpect-4.9.0-py314.patch` both exist.
- **Retrieved:** attempted 2026-11-04. openSUSE returned **HTTP 403**, and redcore served a bot-protection (Anubis) page instead of the patch.
- **Gate:** ❌ the patch text is unreachable. **Source not inspected.** This case is reconstructed from a search-result summary (2026-11-03): *"The pexpect packaging patch sets a module-level context that uses fork when the default is forkserver."*

## What this case represents

This is the **workaround**, not the bug: code that keeps working by forcing fork. forkcheck should flag it as `FA008 explicit-fork-reliance` (info): it works today, but fork is unsafe with threads and is no longer the default anywhere.

| From the summary | Invented for the reconstruction |
|---|---|
| a module-level `get_context("fork")`, used when the default is `forkserver` | the child function and its queue round-trip |
| test processes started from that context | |

## Expected findings

- `FA008` at `distilled.py:16`: `multiprocessing.get_context("fork")`.

## Observed behaviour (2026-11-04, CPython 3.15.1, K2 §5.9 bootstrap)

| method | outcome | output |
|---|---|---|
| fork | exit 0 | `child says: pexpect-child` / `context start method: fork` |
| forkserver | exit 0, **same as fork** | the workaround works: the forced context is `fork` |
| spawn | exit 0, differs (harmlessly) | `context start method: spawn`: the check only special-cases `forkserver`, so under spawn the default context is used. The child still works because its target is module-level |

This case is the "a finding without a breakage" example for the lab: FA008 fires, and the confirmation shows no failure.
