# Errata: Corrections to the Research Notes

> **What this is:** [`python-mastery-research.md`](python-mastery-research.md) (revision 5, 2026-11-03) is the *starting point* for Kiln, but it isn't infallible. This file lists every place where the spec pass, or later milestones, found it **wrong, unverified, incomplete or inapplicable**, together with what Kiln does instead.
> **Rule ([ADR-020](../decisions.md#adr-020)):** when a milestone's verification gate fails, the implementer adds an entry here and links it from the lab report's "Verified facts" table. The research file itself is **never edited**. It stays a faithful record of what we believed at the start.

**Entry format:** research claim → correction → evidence → affected milestone(s) → status.

**Status values:**
- ✅ **corrected**: the spec already reflects it.
- 🔍 **to verify**: a verification gate decides it at implementation time.
- ℹ️ **clarification**: not wrong, but incomplete.

---

## E-01: The WSL distribution is Debian 13, not Ubuntu
- **Research claim:** §4 and §6, "Runs on: WSL2 Ubuntu".
- **Correction:** the machine's WSL2 distribution is **Debian 13 (trixie)**. All setup commands in [spec/07](../spec/07-environment.md) are written for Debian (`apt`, the package names). Nothing in Kiln depends on Ubuntu specifically.
- **Evidence:** `wsl -l -v` and `/etc/os-release` on 2026-11-03: `Debian GNU/Linux 13 (trixie)`, kernel `6.6.87.2-microsoft-standard-WSL2`.
- **Affects:** K0 (environment). [ADR-003](../decisions.md#adr-003).
- **Status:** ✅ corrected

## E-02: `__del__` cycles are not a leak in modern Python
- **Research claim:** §4 K6, "planted leaks (`__del__` cycles, …)".
- **Correction:** since **Python 3.4 (PEP 442, "Safe object finalization")**, the cyclic garbage collector *can* collect reference cycles whose objects define `__del__`. Before 3.4 such cycles were put in `gc.garbage` and never freed, and that's where the old advice comes from. K6 instead plants:
  - cycles while the GC is disabled
  - `__del__` resurrection
  - an unbounded cache
  - a closure capturing a large object in a long-lived registry
  - optionally, a `requests`-style session pattern

  It also *demonstrates* that a plain `__del__` cycle is collected now, as a myth-vs-fact exercise.
- **Evidence:** PEP 442; the `gc` module docs on `gc.garbage` ("objects with `__del__` … no longer end up in `gc.garbage`" since 3.4).
- **Affects:** K6. [ADR-016](../decisions.md#adr-016).
- **Status:** ✅ corrected (K6 still verifies the demonstration on every matrix interpreter)

## E-03: "msgpack calls `gc.enable()`" is historical and unverified for current versions
- **Research claim:** §2.1 and K3, GDB showed that `msgpack` called `gc.enable()` when it loaded, so Instagram had to use `gc.set_threshold(0)`. K3 plans to "catch `msgpack` re-enabling the GC with a `gc.callbacks` hook".
- **Correction:** the claim comes from Instagram's 2017 post, read via a secondary summary, because the primary page was unreachable. Whether **today's** msgpack still toggles the GC at import or during unpacking is **not known**. It may have changed, or the behaviour may have lived only in specific code paths.
  - Also note: a `gc.callbacks` hook fires on *collections*, not on `gc.enable()` calls. Catching "who re-enabled the GC" needs a different technique, such as wrapping `gc.enable` in a Python shim during import, an audit hook, or polling `gc.isenabled()` around each import.
- **Plan:** K3 verification gate:
  1. Run `import msgpack` (and an unpack call) between `gc.disable()` and `gc.isenabled()` checks, on every matrix interpreter.
  2. If msgpack no longer re-enables the GC, K3 uses `labs/k3/rogue_gc_enabler.py`, a stand-in module that calls `gc.enable()` on import, labelled clearly as a stand-in. Optionally it also pins an old msgpack release for historical reproduction, if one installs on 3.13+.
- **Evidence:** pending (K3).
- **Affects:** K3.
- **Status:** 🔍 to verify

## E-04: GIL re-enabling: use our own C extension, not a third-party package
- **Research claim:** §2.9 and K4, "detect which extension re-enables the GIL", which implies finding a real third-party package that does it.
- **Correction:** the claim about *the mechanism* is right: importing an extension module that doesn't declare free-threading support (`Py_mod_gil`) re-enables the GIL on a free-threaded build, with a `RuntimeWarning`. But *which* popular packages still lack the declaration in Nov 2026 changes month to month. So K4 builds its own pair of tiny C extensions, one declaring `Py_MOD_GIL_NOT_USED` and one not, which makes the experiment deterministic. Scanning real installed packages is a bonus, labelled date-dependent.
- **Evidence:** CPython free-threading HOWTO (3.15); the `Py_mod_gil` slot documentation.
- **Affects:** K4. [ADR-015](../decisions.md#adr-015).
- **Status:** ✅ corrected

## E-05: krb5: what actually broke, and how upstream fixed it
- **Research claim:** §00 lists "[MIT krb5 #9197]" as broken by the forkserver default, without detail.
- **Clarification:**
  - **What broke:** `src/tests/t_otp.py` failed under Python 3.14 when its RADIUS daemon was started via `multiprocessing`, because the default start method is now `forkserver`.
  - **Root pattern:** the test script has **no `if __name__ == "__main__":` guard**, so the forkserver/spawn child **re-imports the script** and re-runs its module-level code.
  - **A second, subtler pattern:** the re-import also imports `k5test`, which **registers an `atexit` handler**. In the child that handler prints a spurious "re-run this test script" failure message, because the child never calls `success()`.
  - Moving the body into a function wasn't enough, and the daemon start failed with `ConnectionResetError`.
  - **Upstream fix (PR krb5/krb5#1483):** force `multiprocessing.set_start_method("fork")` in `t_otp.py`. The maintainers noted that `fork` may eventually become unusable, so this is a stopgap.
- **What this means for the checker:** the K2 corpus case `corpus/real/krb5-t_otp/` must trigger:
  - the **missing `__main__` guard** rule
  - an **import-time side effect** rule (`atexit.register` or similar at module level in a module imported by the entry script)
  - the forced-`fork` fix, as an "explicit fork reliance" *informational* finding
- **Evidence:** krb5-bugs mailing list 2026-02 (#9197); GitHub PR krb5/krb5#1483 discussion (fetched 2026-11-03).
- **Affects:** K2. [ADR-017](../decisions.md#adr-017).
- **Status:** ✅ corrected (spec), and K2 builds the distilled reproduction

## E-06: oslo.concurrency: nested functions as targets; two different downstream fixes
- **Research claim:** §00, oslo.concurrency broke with "state not inherited, local functions as targets". The link is to a Gentoo patch.
- **Clarification:**
  - The broken tests in `oslo_concurrency/tests/unit/test_lockutils.py` defined **process targets as local (nested) functions**. Those can't be pickled, so they fail under spawn and forkserver.
  - **Debian's fix** (python-oslo.concurrency 7.2.0-4) moved the logic to module level, which is the real fix.
  - **Gentoo's fix** forced `multiprocessing.get_context("fork")`, which is a workaround.
  - The Gentoo patch URL in the research notes returned **HTTP 404** on 2026-11-03. K2's PROVENANCE file must cite the Debian patch, plus a Gentoo copy if one can be found.
  - No upstream oslo.concurrency fix was found during the spec pass.
- **What this means for the checker:** must trigger the **nested/lambda target** rule, and the **unpicklable argument** rule if locks or handles are passed.
- **Evidence:** sources.debian.org patches for python-oslo.concurrency 7.2.0-4; web search 2026-11-03; the 404 on the ftp.fau.de Gentoo patch URL.
- **Affects:** K2.
- **Status:** ✅ corrected (spec). K2 step 1 re-fetches the sources

## E-07: FSL pyfeeds source was unreachable during the spec pass
- **Research claim:** §00, FSL pyfeeds MR 46, "logging handlers relied on fork".
- **Clarification:** `git.fmrib.ox.ac.uk` refused connections on 2026-11-03, so the exact code couldn't be inspected. The *pattern* is well understood: logging is configured in the parent, and under spawn or forkserver the child process has no handlers, so its log output silently vanishes or goes to the default stderr handler with the wrong format.
- **Plan:** K2 step 1 retries the fetch. If it's still unreachable, the distilled case is built from the described pattern, and PROVENANCE.md says "pattern reconstructed from MR title and description; source not inspected".
- **Affects:** K2.
- **Status:** 🔍 to verify

## E-08: More real-world forkserver breakages found (calibration bonus cases)
- **Research claim:** §00 lists three breakages.
- **Clarification:** the spec-pass search found more, which are useful as extra calibration cases:
  - **git-repo** (Google's `repo` tool): a test patched a **class-level mock** in the parent. The child, created by forkserver, imported a fresh class without the patch, so "state not inherited". The fix moved to a per-instance attribute.
  - **pexpect**: tests needed a **module-level multiprocessing context** forcing `fork` when the default became forkserver.
- **Affects:** K2 (`corpus/real/git-repo-mock/`, `corpus/real/pexpect-context/` are stretch cases; SC-5 is judged on the original three).
- **Status:** ✅ corrected (spec)

## E-09: `ptrace_scope` is 1 on this machine
- **Research claim:** §6, "Tachyon, `asyncio ps` and `pdb -p` need `ptrace_scope=0` or `CAP_SYS_PTRACE`".
- **Clarification:** correct, and observed: `/proc/sys/kernel/yama/ptrace_scope` = **1** (the Debian default). Attach tools will fail until it's relaxed, or until the tool runs with sudo. [spec/07 §5](../spec/07-environment.md#5-ptrace-and-attaching-to-running-processes-k5k6) gives three options, and the preferred one is a temporary `sysctl` for lab sessions only. `kiln doctor` check D11 reports it.
- **Affects:** K5, K6.
- **Status:** ✅ corrected

## E-10: Memory budget is ~7.5 GiB, not unlimited
- **Research claim:** §2.1 describes Instagram's scale (for example "8 GB RAM saved per machine") and K3 reproduces it without stating sizes.
- **Clarification:** WSL sees about **7.5 GiB** on this machine. Kiln's experiments are scaled down: a 300 MiB preload graph with 4 workers, OOM labs inside a 512 MiB cgroup, and every experiment ≤ 3 GiB. Absolute numbers will therefore be much smaller than Instagram's. Lab reports compare **ratios and shapes**, not absolute GB.
- **Affects:** K1, K3, K6, K7. [ADR-023](../decisions.md#adr-023).
- **Status:** ✅ corrected

## E-11: The Tachyon "admin rights on Windows" note doesn't apply
- **Research claim:** §2.8 and §6, Tachyon "works on Windows with admin rights or SeDebugPrivilege".
- **Clarification:** true but irrelevant, because Kiln never runs natively on Windows (ADR-003). In WSL the requirement is Linux ptrace permission (E-09). The note still matters for the sibling Cartographer project.
- **Affects:** K6.
- **Status:** ℹ️ clarification

## E-12: No C toolchain or Rust is installed yet
- **Research claim:** §4, "Use `uv python install …`", with K4 (implicitly) and K8 (maturin) needing compilers.
- **Clarification:** on 2026-11-03, **neither `gcc` nor `cargo` was installed** in WSL. [spec/07 §2](../spec/07-environment.md#2-one-time-system-packages-k0-step-1) adds `build-essential`, and §9 adds rustup. `kiln doctor` checks D9 and D10 catch them.
- **Affects:** K0, K4, K8.
- **Status:** ✅ corrected

## E-13: Exact patch interpreters for K7 may not be installable
- **Research claim:** §4, `uv python install 3.13 3.14.4 3.14.5 3.15 3.15t`, and §2.2 says 3.14.5 (10 May 2026) reverted the incremental GC.
- **Clarification:** both the availability of the exact patches in uv's python-build-standalone index *and* the revert itself have to be checked:
  - the revert by observing GC behaviour (thresholds and `gc.get_stats()` shape, and the peak-RSS difference on the K7 workload)
  - against the 3.14.5 changelog
- **Plan:** K0 gate (installability) and K7 gate (behaviour). If either fails, the K7 fallback is documented in the K7 spec.
- **K0 result (2026-11-03, gate G0.1):** `uv python install 3.14.4 3.14.5` succeeded (uv 0.13.5), so installability is ✅. The behaviour half is still K7's job.
- **Affects:** K0, K7.
- **Status:** 🔍 to verify (installability ✅ verified in K0; behaviour pending K7)

## E-14: `gc.callbacks` is a collection hook, not a general GC-state hook
- **Research claim:** §4 K3, "Catch `msgpack` re-enabling the GC with a `gc.callbacks` hook".
- **Clarification:** `gc.callbacks` functions are called with `"start"`/`"stop"` phases **around each collection**. They're excellent for K7's **pause histogram**, but they aren't notified when someone calls `gc.enable()`. See E-03 for the technique K3 uses instead. One indirect signal does exist: callbacks *start firing again* after a re-enable. K3's primer may present that as a clever detection trick, clearly explained.
- **Affects:** K3, K7.
- **Status:** ✅ corrected (spec)

## E-15: Copy-on-write: `gc.freeze()` doesn't stop refcount writes
- **Research claim:** §2.1 presents `gc.freeze()` as "Fix 2" for copy-on-write sharing.
- **Clarification:** `gc.freeze()` stops the **GC** from writing to old objects: it no longer scans them or touches their GC headers. Simply **reading** an object in Python still writes to its **reference count** (on the GIL build), which also dirties the page. So `freeze` reduces copy-on-write faults but can't eliminate them. Only **immortal objects** (PEP 683, 3.12+, for a limited set such as small ints, `None` and interned strings), or not touching the objects, avoid refcount writes.
  - The free-threaded build uses **biased reference counting**, which changes this picture again.
  - This isn't an error in the research notes, but the K3 hypothesis must account for it. Otherwise the learner would expect `freeze` to restore 100% sharing.
- **Affects:** K3 (and K4 for the free-threaded aside).
- **Status:** ℹ️ clarification

## E-16: Forked workers skip interpreter finalization anyway
- **Research claim:** §2.1, Instagram registered `atexit.register(os._exit, 0)` first, so that it runs last and skips the final GC at shutdown.
- **Clarification:** Kiln's *forked* workers always end with `os._exit()` ([code-style §7 rule 6](../standards/code-style.md#7-fork-safety-rules)). That's required for correctness, so that the child never returns into the master's code. It means forked workers never run the final GC to begin with. The `worker.exit_via_os_exit` switch therefore only makes a measurable difference for **spawned** workers, and for the master itself. The K3 lab must measure the trick where it applies and explain why it's a no-op elsewhere.
- **Affects:** K3.
- **Status:** ✅ corrected (spec)

## E-17: The newest 3.15 patch is 3.15.1, not 3.15.2
- **Research claim:** the summary says Python 3.15 is "now 3.15.2".
- **Correction:** on 2026-11-03, uv 0.13.5's index offered **3.15.1** as the newest 3.15 (and 3.15.1+freethreaded). That's what Kiln develops against. Nothing in the plan depends on the patch number, and ExperimentRecords store the exact version anyway.
- **Evidence:** `uv python list --all-versions` and `uv python install 3.15 3.15t` in the K0 setup log ([tours/K0.md](../tours/K0.md#appendix-setup-log)).
- **Affects:** K0 (records only).
- **Status:** ✅ corrected

## E-18: systemd stops a whole scope after an OOM kill (OOMPolicy=stop)
- **Spec claim:** spec/07 §6.1 ran the OOM lab with `systemd-run --user --scope -p MemoryMax=512M -p MemorySwapMax=0`.
- **Correction:** systemd's default `OOMPolicy=stop` makes it stop the entire unit, sending SIGTERM to every process (including the Kiln master), right after the kernel OOM-kills any one of them. The first K1 OOM run showed the master receiving SIGTERM within milliseconds of the worker's OOM kill. Adding `-p OOMPolicy=continue` keeps the rest of the scope alive.
- **Evidence:** K1 lab (a), log `signal_received signal=SIGTERM` right after `worker_died … oom_confirmed=true`. See [labs/K1-supervisor.md](../labs/K1-supervisor.md).
- **Real-world lesson:** container runtimes and systemd services make the same choice. "The OOM killer killed one process" can turn into "the platform stopped everything", which is another way an operator ends up seeing only "worker lost".
- **Affects:** K1 (fixed in `labs/k1/k1_lab.py` and spec/07 §6.1).
- **Status:** ✅ corrected
