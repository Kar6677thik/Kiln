# K10 — Capstone: Acceptance, Final Docs Audit, Retrospective, and Teach-It-Back

> **Status:** 📝 specified · **Depends on:** K0–K9 (all ✅) · **Branch:** `milestone/k10-capstone` · **Size:** S (4 steps)
> **Incident:** all of them. K10 checks that the whole system meets [spec/00 §5](../spec/00-overview.md#5-success-criteria-kiln-is-done-when) (SC-1…SC-7), and that the learner can explain what was built.
> **Reference implementations (read *after* building):** gunicorn (`gunicorn/arbiter.py`, `gunicorn/workers/`) and Celery (`celery/worker/`, `celery/concurrency/prefork.py`, billiard), read now as a *whole* to compare architectures

---

## 1. The story (plain English)

You've built a job server one piece at a time. A master supervises workers. Jobs flow over a protocol we designed. Workers start three different ways. Memory is shared, leaked, collected and measured. Threads, subinterpreters and event loops run jobs side by side. An HTTP front door has a watchdog. A Rust module crosses the language boundary. And a benchmark report checks all of it across five interpreters.

A capstone answers two questions. **Does the whole thing work, as promised in the overview?** That's the seven success criteria, run again *together*, on the final code, with real numbers. **Do you understand it?** Building something isn't the same as being able to explain it. So K10 ends with a **teach-it-back** exercise list, a cold-reader audit of the documentation, and a retrospective that ties every row of the knowledge map (research §3) to the exact code, primer and lab report that demonstrate it.

**In this milestone we will:**
1. Write one script that runs every acceptance check and produces a pass/fail table.
2. Fix anything that fails.
3. Have a fresh reader with no context audit the docs as a beginner would.
4. Write the retrospective, including an honest comparison with gunicorn and Celery.
5. Hand you the teach-it-back exercises and the sign-off checklist.

## 2. What you will learn

- **Explain**, end to end, the life of a job through Kiln, including every failure path (spec/03 §4, spec/04). *(All areas)*
- **Compare** Kiln's architecture with gunicorn's and Celery's, and justify each difference with an ADR. *(Processes, Concurrency)*
- **Evaluate** your own design decisions in hindsight: which ADRs held up, and which would you change? *(Experiment design / engineering judgement)*
- **Teach** each knowledge-map concept back in your own words. That's the real test of G1 in spec/00 §3.

## 3. Entry criteria

- Every milestone K0–K9 is ✅ in [`README.md`](README.md)'s status table (learner sign-off recorded).
- `docs/questions.md` has no open questions older than one milestone.
- `results/published/K9/<plan_id>/report.html` exists.

## 4. Scope

### In scope
- `labs/k10/acceptance.py` (lab id `k10-accept`): an automated SC-1…SC-7 run, writing an acceptance record and a Markdown table.
- Fixes for any acceptance failure. Each fix is a normal commit with a test. A failure that reveals a spec problem gets an ADR.
- The cold-reader documentation audit and its fixes.
- `docs/labs/K10-capstone.md`: the retrospective.
- `docs/tours/K10.md`: the "grand tour" of the whole codebase in one reading path.
- The teach-it-back exercises and the sign-off checklist (in the retrospective).

### Out of scope (and why)
- New features. Ideas go to "Stretch / later" lists, or to Cartographer.
- Performance tuning to "pass" SC-1. SC-1 is about correctness under load (0 errors), not a speed target. Throughput is recorded, not judged.

## 5. Design

### 5.1 The acceptance harness (`labs/k10/acceptance.py`)

- **Command:** `./scripts/dev.sh lab k10-accept [--only SC-n ...] [--keep-going]`.
- **Where it runs:** SC-1/2/3 run from the ext4 snapshot (`./scripts/dev.sh bench-snapshot`), because SC-1's throughput is recorded. The interpreter is the dev interpreter, 3.15. SC-7 covers the others.
- **Outputs:**
  - one ExperimentRecord per SC check, under `results/K10/acceptance/` (experiment `acceptance`, variant `SC-n`, `metrics.pass` = `{unit:"count", value: 1|0}`, plus each check's own metrics)
  - `results/published/K10/acceptance-<date>.md`: the summary table pasted into the lab report
- **Every check** starts its own server where one is needed (port 0, with the bound port read from the master's `listening` log event, per spec/09 §4.3), and tears it down. The harness exits 0 only if every check passes.

| SC | Exact procedure | Pass threshold |
|---|---|---|
| **SC-1** 10,000 jobs under load | `kiln serve examples.demo:app --labs --workers 4 --pidfile $TMP/kiln.pid`, then `kiln bench load --jobs 10000 --concurrency 64 --task labs.echo --kwargs '{"value":"x"}' --out $RESULTS`, **3 independent runs** (a fresh server each) | in **all 3** runs: 10,000 jobs SUCCEEDED, 0 client errors, 0 jobs in any other terminal state (checked through KP `stats` `jobs.by_state` after the run). Throughput and p50/p99 are recorded and reported, not judged |
| **SC-2** zero dropped jobs on reload | the same server, then `kiln bench load --jobs 10000 --concurrency 64 --task labs.sleep --kwargs '{"ms":5}' --reload-every 2 --pidfile $TMP/kiln.pid`, 3 runs | in all 3 runs: 10,000 SUCCEEDED; **0** `LOST`, `CANCELLED`, `FAILED`, `TIMED_OUT`; master log shows **≥ 5** `reload_started` *and* ≥ 5 matching `reload_finished` events (spec/06 §2.3) during the run, with `forced_kills=0` on each |
| **SC-3** OOM-killed workers detected | `./scripts/dev.sh lab k1-oom` (spec/07 §6, K1 §7a): a server inside a 512 MiB cgroup, `kiln submit labs.alloc --kwargs '{"mb":700,"hold":true}' --wait` | the job's final JobView has `state == "LOST"` and `error.kind == "worker_lost"`; the master log has `worker_died` (spec/06 §2.2) with `how=killed signal=SIGKILL suspected_oom=true`; **and** its `oom_evidence` shows the cgroup `oom_kill` counter increasing (spec/04 §4.2's "confirmed" case), *or* the K1 gate G1.4 fallback is documented in errata, in which case `suspected_oom=true` alone passes, with the harness printing "SC-3 passed on fallback evidence" |
| **SC-4** before/after write-ups | `./scripts/dev.sh docs-check --only lab-reports` (the `--only` filter is added to `check_docs.py` in step 1) | every `docs/labs/K1…K9-*.md` has all of documentation.md §6's required sections; no empty result cells (`not measured` + a reason is allowed); every cited `results/published/...` path exists |
| **SC-5** K2 catches the real breakages | for each case in `krb5-t_otp`, `oslo-lockutils`, `fsl-pyfeeds`: `kiln forkcheck corpus/real/<case> --confirm corpus/real/<case>/distilled.py --format json` (K2 AC-2.4) | each case has ≥ 1 **confirmed** finding whose rule is in that case's `expected.json` "must fire" list |
| **SC-6** documentation complete | `./scripts/dev.sh docs-check` (full) **+** `docs/concepts/README.md` and `docs/python/README.md` indexes have every row ✅ **+** the cold-reader audit (§5.2) has zero open items | all three hold |
| **SC-7** tests on all interpreters | `./scripts/dev.sh test-matrix` | green on 3.13, 3.14, 3.15 and 3.15t. Skips are allowed only with a marker reason (`requires_314`, `requires_315`, `requires_ft`, `requires_native`) and are listed in the report. On 3.15t, the conftest GIL check (spec/09 §8) must not have flagged the session |

### 5.2 The cold-reader documentation audit

**Why:** whoever wrote the docs can't see their gaps, because they already know the answers. A *cold* reader can.

1. Start a **fresh agent with no conversation context** (a new assistant session). Brief it: *"You are new to Python and to operating systems. Read the Kiln docs in the order given by `docs/README.md`. For every sentence you can't understand from what you've read so far, record: the file:line, the term or jump that lost you, and what you expected to find. Don't use outside knowledge to fill gaps; a gap is a finding."*
2. The findings go into `docs/questions.md` under "K10 audit", one row each.
3. The implementer resolves each one **in the docs**, by defining the term earlier, adding a glossary entry, adding a diagram or reordering, and marks it ✅ with a link to the fix.
4. Repeat the audit once on the changed sections only. SC-6 needs **zero** open items after the second pass.
5. **The learner's own pass:** the learner reads `docs/tours/K10.md` (the grand tour) and adds anything unclear to the same list. These count toward SC-6 too.

### 5.3 The retrospective (`docs/labs/K10-capstone.md`)

It follows the lab-report template's spirit, with these sections (checked by `check_docs.py`, which step 1 extends with a K10 profile):

1. **Acceptance results:** the SC table from §5.1, with record links and the commands to reproduce.
2. **The knowledge map, revisited:** a table with one row per research §3 area (Data model, Runtime internals, Import system, Concurrency, Memory & perf, Typing, Native code, Packaging & security, Testing & ops). Columns: `area | what you can now explain (3–6 bullets) | where Kiln demonstrates it (file:line links) | primers | lab reports | coverage level (primary / secondary / not covered, with a reason)`. Rows Kiln doesn't cover (Typing, Packaging & security, mostly) say so and point to Cartographer (research §7).
3. **The life of a job, end to end:** one large ASCII sequence diagram covering the gateway → master → worker path, plus every failure branch, with each arrow linked to its spec section.
4. **Kiln vs gunicorn vs Celery:** a comparison table with these rows: process model; dispatch (push over socketpair vs shared accept vs broker); queue location and durability; reload semantics; recycling (`max_jobs`/`max_rss` vs `max_requests` vs `max-tasks-per-child`/`max-memory-per-child`); death and OOM detection; start methods; in-worker concurrency (executors vs worker classes vs pools); backpressure; observability; configuration. Each cell links to the reference source file read. Each Kiln cell links to its ADR.
5. **Decisions revisited:** for every ADR, one line: *held up* / *would change* + why, with measured evidence where it exists. For example, ADR-007's master-as-bottleneck claim gets checked against K1's ceiling experiment and K9 `core.load`.
6. **Measured highlights:** the five to ten most important numbers from K3–K9, each with its CI and spec/10 wording, linked to the K9 report.
7. **What surprised us:** a list collected from every lab report's Analysis section.
8. **Research errata summary:** what the original research got wrong or out of date, from `docs/research/errata.md`.
9. **Limitations:** WSL, a single machine, a laptop, no durability, the security posture (ADR-021).
10. **Teach-it-back exercises** (§5.4).
11. **Sign-off checklist** (§5.5).
12. **Where to go next:** Cartographer (research §5), plus consolidated Stretch items from K1–K9, deduplicated.

### 5.4 Teach-it-back exercises (in the retrospective)

These are for the learner. There's no code to write (ADR-001). Each exercise names the docs that support it. The learner writes the answers in `docs/teach-back.md`, and the implementer then reviews them **only to find documentation gaps**, which feed back into §5.2. They are never graded.

1. Explain to a friend, in under 200 words, what happens between `kiln submit add --args '[2,3]' --wait` and the `5` appearing.
2. Draw (on paper or in ASCII) the master's event loop and name every kind of event it handles.
3. A worker vanishes. List every way Kiln can find out, and what it does for each.
4. Explain `fork`, `spawn` and `forkserver` using one concrete bug each from K2's corpus.
5. Explain to an Instagram engineer why `gc.freeze()` helped them and what it *didn't* fix (refcount writes).
6. Predict, then check in the K4 report: 8 threads of `burn_cpu` on 3.15 vs 3.15t.
7. Explain why one `time.sleep` in an async handler hurt *every* request, and how the watchdog caught it.
8. Pick one K6 leak and explain how you'd find it in a production service you've never seen.
9. Explain the pause-time vs memory trade-off using the K7 numbers.
10. Explain why the Rust validator did or didn't speed up Kiln end to end, using Amdahl's law and K8's profile.
11. Read one row of the K9 report aloud, including its CI, using the correct wording.
12. Pick one ADR you'd change now, and argue for it.

### 5.5 Sign-off checklist (in the retrospective; the learner ticks it)

- [ ] I ran `./scripts/dev.sh lab k10-accept` myself and saw it pass (or saw and understood each failure's resolution)
- [ ] I read the grand tour (`docs/tours/K10.md`) end to end
- [ ] I completed the teach-it-back exercises in `docs/teach-back.md`
- [ ] Every question I had is answered in the docs (`docs/questions.md` has none open)
- [ ] I can open any file in `src/kiln/` and explain why it exists
- [ ] I agree that G1–G5 (spec/00 §3) are met, or I've written down which aren't and why
- [ ] Approved: merge `milestone/k10-capstone` and mark Kiln ✅

### 5.6 The grand tour (`docs/tours/K10.md`)

One reading path through the whole codebase, in about 25–40 stops, following a job's life: `cli.py` submit → `protocol/framing.py` → `master/arbiter.py` loop → `jobs.py` state machine → `dispatcher.py` → `launch/*` → `worker/main.py` → `executors/*` → back through `result` → `supervisor.py` death handling → `gateway/*` → `memory/*` → `native/*` → `bench/*`. Each stop links to the milestone tour that covers it in depth. It follows the code-tour template (documentation.md §5), and its quiz combines the hardest questions from K1–K9.

## 6. Steps

1. **The acceptance harness + the `check_docs.py` K10 profile.**
   - *Tests:* a harness dry-run mode (`--dry-run`) lists the checks and commands without running them; the SC-4 checker correctly fails a crafted incomplete report.
2. **Run acceptance, fix failures, run again until green.** Publish `results/published/K10/`. Every fix needs a regression test, and the lab report lists each fix.
3. **The cold-reader audit (§5.2), two passes, and all fixes.** This includes the learner's own pass over the grand tour once it's written.
4. **Retrospective, grand tour, teach-back exercises, sign-off checklist.** Update the README status table and the top-level `README.md` ("Kiln is complete; start reading here").

## 7. Lab design

K10 isn't an experiment. It's an **acceptance test**. The "lab" is the SC table.

- **Question:** does the final Kiln meet SC-1…SC-7 on the final commit?
- **Implementer's hypothesis:** SC-1, SC-2 and SC-5 pass (they passed in K1/K2), and SC-4/SC-6 surface a handful of documentation gaps that need fixing.
- **Variables:** none (fixed commit, fixed procedures).
- **Repeats:** SC-1 and SC-2 run 3 times each (§5.1). The others run once, since they're deterministic checks.
- **Procedure:** `./scripts/dev.sh bench-snapshot && cd ~/kiln-bench && ./scripts/dev.sh lab k10-accept`.
- **Result table** (headers only): `SC | check | runs | passed | key numbers (e.g. throughput, reloads seen, oom evidence) | record path | notes`.

## 8. Verification gates (ADR-020)

| # | Claim to verify | How to check | If false → fallback |
|---|---|---|---|
| G10.1 | K1's OOM gate outcome (G1.4) still holds on the final machine state, for example after WSL updates | `./scripts/dev.sh lab k1-oom` | apply the documented G1.4 fallback (SC-3 on suspected evidence) and note it in errata |
| G10.2 | every interpreter in ADR-004 is still installed and resolvable (uv may have pruned some) | `kiln doctor` | reinstall with `uv python install …`, or apply K7's G7.1 substitution, and record it |
| G10.3 | a fresh, context-free reader is available for the audit | start a new session or subagent | the learner performs the audit alone, following §5.2's brief |
| G10.4 | the reference sources (gunicorn, Celery) are reachable for the comparison | clone the repos (read-only) into `/tmp` | use the versions already read during K1–K9 (their links in the lab reports) |

## 9. Acceptance criteria

- **AC-10.1** `./scripts/dev.sh lab k10-accept` exits 0 on the final commit, and its summary is published to `results/published/K10/`.
- **AC-10.2** The SC table in `docs/labs/K10-capstone.md` shows every SC passed, each with a record link and a reproduction command. Any fallback evidence (SC-3) is explicitly labelled.
- **AC-10.3** The cold-reader audit's findings are all ✅ in `docs/questions.md` after the second pass.
- **AC-10.4** The retrospective has all 12 sections in §5.3, including the knowledge-map table with links for every covered row, and the gunicorn/Celery comparison with source links.
- **AC-10.5** `docs/tours/K10.md` exists with ≥ 25 stops, each linking to its milestone tour.
- **AC-10.6** The learner has ticked the sign-off checklist (§5.5), which is recorded in the retrospective, and the README status table shows K10 ✅.

## 10. Docs deliverables

| Kind | Path | Must cover |
|---|---|---|
| Lab / retrospective | `docs/labs/K10-capstone.md` | §5.3, all 12 sections |
| Tour | `docs/tours/K10.md` | §5.6 |
| Teach-back | `docs/teach-back.md` | the §5.4 exercises (questions only; the learner writes the answers) |
| Questions | `docs/questions.md` | the "K10 audit" section with every finding resolved |
| Index updates | `docs/concepts/README.md`, `docs/python/README.md`, `docs/glossary.md`, `docs/faq.md` | final, complete, all ✅ |
| Roadmap | `docs/milestones/README.md` | all milestones ✅ |
| Top-level | `README.md` | updated "start here" for a finished project |

There are no new concept primers in K10. If the audit shows one is missing, it's written then, and listed in the retrospective as "added during K10".

## 11. Reference comparison assignment

Read each as a **whole system** this time, not one feature at a time:
1. **gunicorn:** `arbiter.py` (`run`, `handle_*`, `spawn_worker`, `reap_workers`, `murder_workers`, `manage_workers`) and `workers/base.py`. *Questions:* map each method to its Kiln equivalent. Where does gunicorn's shared-accept design force it to differ from ours (ADR-007)? What does it do that Kiln doesn't (for example `USR2` binary upgrade, which our non-goals excluded)?
2. **Celery:** `celery/concurrency/prefork.py` + billiard's `pool.py` (the task-to-child protocol, lost-worker handling, `maxtasksperchild`) and `celery/worker/request.py` (acks and `acks_late`). *Questions:* how does Celery decide a task is "lost"? How does `acks_late` relate to our at-most-once vs at-least-once (ADR-010)? Where does the broker sit in their design, and what would Kiln gain or lose with one (ADR-011)?

## 12. Risks and pitfalls

- **"Fixing" acceptance by weakening the check.** The thresholds in §5.1 are fixed. Changing one needs an ADR with the learner's agreement.
- **Machine drift since the original labs** (a WSL kernel update, uv pruning interpreters). G10.1 and G10.2 catch it.
- **An audit that's too gentle.** The cold reader must be explicitly told that gaps are findings, and not to fill them with outside knowledge.
- **The retrospective becoming a victory lap.** "Decisions revisited" and "Limitations" are required sections, and the reviewer checks that they contain real criticism.

## 13. Checkpoint quiz (seed questions)

1. Which success criterion would fail first if ADR-007 (push over socketpairs) were replaced by gunicorn-style shared accept, and why?
2. SC-2 passed. What exactly guarantees that no job was dropped during a reload?
3. SC-3 can pass on "suspected" evidence. What is the difference from "confirmed", and why do we allow the fallback?
4. Why does SC-1 judge errors and not throughput?
5. Name one ADR that the measurements proved right and one they challenged.
6. Which knowledge-map rows does Kiln *not* cover, and where would you learn them?
7. In one paragraph: Kiln vs Celery. What would you have to add to Kiln to replace Celery in production?
8. What did the cold-reader audit find that the author couldn't see, and why?

## 14. Stretch / later

- Cartographer (research §5), the sibling project.
- A durability layer (a SQLite write-ahead journal), deliberately excluded by ADR-011, as a follow-up project.
- A screencast or written "walkthrough of the life of a job" built from the grand tour, for teaching someone else.
