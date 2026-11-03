# Kiln Documentation: Start Here

You're new to Python and to systems programming, so these docs assume **nothing**. If something is unclear, that's a bug in the docs: write it in [`questions.md`](questions.md) and it will be fixed.

## Reading order

### Right now (spec pass)
1. [`spec/00-overview.md`](spec/00-overview.md): what Kiln is, the goals, and what "done" means. *(about 15 min)*
2. [`milestones/README.md`](milestones/README.md): the roadmap. *(5 min)*
3. Skim [`spec/01-architecture.md`](spec/01-architecture.md), looking only at the diagrams. Don't worry about the words you don't know yet: K0 and K1 teach them. *(10 min)*
4. Look things up in [`glossary.md`](glossary.md) whenever a word is new.

### When K0 is done
5. The Python starter notes in [`python/`](python/README.md), in the listed order.
6. The first concept primers in [`concepts/`](concepts/README.md): the terminal, processes, files and fds.
7. The [K0 code tour](tours/K0.md).

### For every milestone after that
Spec → primers → code tour → lab report (**fill in your prediction first**) → run it yourself → quiz. The full procedure is in [`standards/milestone-workflow.md` §5](standards/milestone-workflow.md#5-how-the-learner-reviews-suggested-about-13-hours-per-milestone).

## Map of the docs

| Folder / file | What's inside | Who writes it, when |
|---|---|---|
| [`spec/`](spec/) | **What** the system does: architecture, protocol, jobs, supervisor, config, observability, environment, layout, testing, measurement | spec pass (done) |
| [`milestones/`](milestones/README.md) | **The plan**, one spec per milestone K0–K10 | spec pass (done) |
| [`decisions.md`](decisions.md) | **Why**: every architecture decision | spec pass, plus implementers |
| [`standards/`](standards/) | **Rules**: documentation, code style, workflow | spec pass (done) |
| [`templates/`](templates/) | Skeletons for primers, notes, tours, lab reports, ADRs, milestone specs | spec pass (done) |
| [`concepts/`](concepts/README.md) | Primers on systems and runtime concepts | each milestone, before its code |
| [`python/`](python/README.md) | Primers on Python language features, plus the ledger | K0 starter set, then as features appear |
| [`tours/`](tours/) | Guided reading paths through the code | end of each milestone |
| [`labs/`](labs/) | Experiment reports with real numbers | each milestone |
| [`glossary.md`](glossary.md) | Every term, in plain English | continuously |
| [`faq.md`](faq.md) | Common "but why...?" questions | continuously |
| [`questions.md`](questions.md) | **Your** open questions | you ask, implementers answer |
| [`research/`](research/) | The original research and its errata | spec pass |
