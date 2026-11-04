# Provenance: krb5-t_otp (required for SC-5)

- **Upstream:** MIT krb5, `src/tests/t_otp.py`. Bug report [krb5-bugs #9197](https://mailman.mit.edu/pipermail/krb5-bugs/2026-February/013527.html). Fix: [PR #1483](https://github.com/krb5/krb5/pull/1483), merge commit `c2697c127be660cfe2a623e153c347ade50f184b` (merged 2026-03-30). The pre-fix code is that commit's parent; `scripts/fetch-corpus.sh` checks it out.
- **Retrieved:** 2026-11-04. The current `t_otp.py` was fetched from GitHub (raw). The PR page was fetched. The pre-fix file itself was not cloned during the corpus pass.
- **Gate G2.6:** ✅ reachable.

## The upstream pattern (short quotes, © MIT krb5, for attribution)

From the current `t_otp.py` (after the fix):

```python
from k5test import *
class RadiusDaemon(multiprocessing.Process):
# "forkserver" relies on being able to re-import the invoking module.
# That doesn't work for k5test scripts as we don't use __name__ ==
multiprocessing.set_start_method('fork', force=True)
daemon = UDPRadiusDaemon(args=(server_addr, secret_file, 'accept', queue))
daemon.start()
success('OTP tests')
```

The PR's commit message: *"Set the multiprocessing start method to "fork" in t_otp.py, as the new default "forkserver" method is not compatible with our test scripts."* The discussion on the PR records that importing the script in the child also imports `k5test`, whose atexit handler prints the "re-run this test" text when `success()` wasn't called.

## What the distilled version keeps and simplifies

| Kept | Simplified |
|---|---|
| No `__main__` guard: the whole test body is top-level code | the real RADIUS protocol and pyrad → a queue round-trip |
| The daemon is a `multiprocessing.Process` **subclass**, started at top level | three daemon classes → one |
| A helper module registers an **atexit** banner on import, silenced by `success()` | `from k5test import *` → `import k5test_like` (keeps ruff clean; same side effect) |
| | the fix (`set_start_method('fork')`) is **removed**, so this is the pre-fix behaviour |

## Expected findings (`expected.json`)

- `FA001` at `distilled.py:32`: `daemon.start()`, the top-level start of a Process-subclass instance, outside any guard.
- `FA005` at `k5test_like.py:22`: `atexit.register(...)` at import time, in a module imported by a script that starts processes.
- `FA008` may fire only if someone re-adds the upstream fix.

## Observed behaviour (2026-11-04, CPython 3.15.1, K2 §5.9 bootstrap, cwd = this folder)

| method | outcome | output |
|---|---|---|
| fork | exit 0 | `daemon: started` / `daemon: accept:otp-token` / `*** Success: OTP tests` |
| forkserver | exit 0, **differs** | the child re-imports the script and fails: `RuntimeError: An attempt has been made to start a new process before the current process has finished its bootstrapping phase.` The parent prints `daemon never answered` after 5 s. The child prints the bogus `*** Failure: test script did not complete; re-run this test` banner (the k5test atexit symptom) |
| spawn | exit 0, **differs** | same as forkserver |

The parent still exits 0, because the queue timeout is caught. "Differs" is judged on the output, as in K2 §5.9.
