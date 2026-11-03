# Milestone Workflow and Definition of Done

> **Binding for every implementation round.** This is the procedure the implementer follows for each milestone, and the checklist the learner uses to sign off.

---

## 1. Before starting a milestone

1. Read, in order: [`spec/00-overview`](../spec/00-overview.md), the **milestone spec** (`docs/milestones/Kn-*.md`), every spec section it references, [`decisions.md`](../decisions.md), and [`documentation.md`](documentation.md).
2. Check `docs/questions.md` for open learner questions related to this milestone. They must be answered in this milestone's docs.
3. Check the **Entry criteria** in the milestone spec. If an earlier milestone isn't done, stop and say so.
4. Create the branch `milestone/kN-<slug>` ([ADR-025](../decisions.md#adr-025)).
5. Run `./scripts/dev.sh doctor` and record the output in the lab report's Setup section.

## 2. During the milestone: step by step

Each milestone spec breaks the work into numbered **steps**. For **each step**:

| Phase | What happens |
|---|---|
| **a. Primer first** | Write or extend the concept primers and Python notes the step needs, *before* the code. Ask: "if the learner read only these docs, would the code make sense?" |
| **b. Verify facts** | Run that step's **verification gates** and record them in the lab report. If a gate fails, apply the documented fallback, write an errata entry, and add an ADR if a design decision changes |
| **c. Tests first where practical** | Write tests that pin down the spec'd behaviour, each with a docstring |
| **d. Code** | Implement, following [`code-style.md`](code-style.md) and the in-code documentation rules |
| **e. Run** | `./scripts/dev.sh check`: ruff, mypy, pytest, `check_docs.py`. All green |
| **f. Commit** | One commit per step. The message says what and why in plain English |
| **g. Step summary** | Append to the milestone's code tour draft: what was built and which files |

**Scope discipline:** do what the step says, and do it completely. Ideas for more go into the milestone's "Stretch / later" list, not into the code.

**Spec gaps:** if the spec is silent or ambiguous on something that matters:
1. Pick the option most consistent with the existing ADRs.
2. Record it as a new ADR marked `Accepted (implementer, pending learner review)`.
3. Mention it in the end-of-milestone summary.
Don't stall on trivia. *Do* stop and ask if the choice changes user-visible behaviour, scope, or a learning objective.

## 3. Finishing the milestone

1. Run the full **lab**: before → fix → after, with the repeats required by [spec/10](../spec/10-measurement-methodology.md). Publish the cited records to `results/published/Kn/`.
2. Write the **lab report** with every section filled in, real numbers, and the reference comparison.
3. Write the **code tour** `docs/tours/Kn.md`.
4. Update `docs/glossary.md`, `docs/python/README.md` (the ledger), `docs/concepts/README.md` (the index), `docs/faq.md`, and the milestone status table in [`../milestones/README.md`](../milestones/README.md).
5. Run `./scripts/dev.sh test-matrix` (3.13, 3.14, 3.15, 3.15t). Record skips and why.
6. Write the **end-of-milestone summary** for the learner, in plain language:
   - what was built
   - what was learned
   - what surprised us
   - which decisions were made (any new ADRs)
   - what to read first
   - how long the reading will take, roughly
7. Ask the learner to review. Merge only after sign-off.

## 4. Definition of Done (DoD): the checklist

A milestone is **done** only when every box is ticked. Copy this checklist into the PR or summary.

**Functionality**
- [ ] Every acceptance criterion in the milestone spec passes, each one linked to the test or lab section that shows it
- [ ] `./scripts/dev.sh check` is green
- [ ] `./scripts/dev.sh test-matrix` is green (version-gated skips listed with their reasons)

**Learning material**
- [ ] Every primer and Python note in "Docs deliverables" exists and has all required sections
- [ ] Every new Python construct has a ledger row and a note
- [ ] Code tour written, with ≥ 5 stops, "Change it and see" exercises and a quiz
- [ ] Lab report complete: real numbers, verified-facts table, reference comparison, empty prediction box for the learner
- [ ] Glossary, FAQ and the concepts index updated
- [ ] Open learner questions for this area answered in the docs

**Hygiene**
- [ ] No `TODO` without an issue reference in `docs/milestones/Kn` "Stretch / later"
- [ ] No dead code; `# LAB:` code is isolated in `labs/` or `kiln/labs/`
- [ ] ADRs added for any decision taken; errata updated for any research claim found false
- [ ] Milestone status table updated

## 5. How the learner reviews (suggested, about 1–3 hours per milestone)

1. Read the end-of-milestone summary.
2. Open the lab report, **fill in "Your prediction"**, then read on.
3. Read the primers listed at the top of the code tour.
4. Follow the code tour, and try at least one "Change it and see".
5. Run the lab commands yourself and compare your numbers.
6. Take the quiz. Anything you got wrong *and* the explanation didn't fix goes into `docs/questions.md`.
7. Say "approved" (merge) or list what's unclear (the implementer fixes the docs, then you review again).
