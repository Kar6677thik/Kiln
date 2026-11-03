<!--
TEMPLATE: lab report. Copy to docs/labs/Kn-<slug>.md. Rules: docs/standards/documentation.md §6, spec/10 §9.
- Keep all 13 headings EXACTLY as written (checked by scripts/check_docs.py).
- NUMBERS ARE NEVER INVENTED. Every number links to a record in results/published/. A missing measurement says
  "not measured: <reason>". An empty table cell fails the docs check.
- The implementer NEVER fills in "Your prediction".
Delete these comments when done.
-->

# Lab Kn — <title>

> **Milestone:** [Kn](../milestones/Kn-<slug>.md) · **Incident:** research §2.x · **Status:** draft / final · **Commit:** `<hash>`

## 1. The incident

<!-- Retell the real story in plain words (who, what broke, how they noticed, how they fixed it). 2–4 paragraphs. -->

## 2. Your prediction

> ✍️ **Learner: fill this in BEFORE reading further.** What do you think will happen when we <do X>? Which number will change, and by roughly how much?
>
> _(your answer here)_

### Implementer's hypothesis

<!-- What the implementer expects, WITH the reasoning, written before running the experiment. -->

## 3. Verified facts

| # | Claim (source) | Command run | Observed | ✅/❌ |
|---|---|---|---|---|
| 1 | <e.g. "3.14.5 uses the generational GC" (research §2.2)> | `<command>` | `<output>` | |

<!-- Any ❌ → describe the fallback used, and link the errata entry in docs/research/errata.md. -->

## 4. Setup

- **Interpreter(s):** <versions + builds>
- **Commit:** `<hash>` (dirty: no)
- **Machine:** WSL2 Debian 13, kernel <…>, <N> CPUs, <N> GiB RAM; source location: <ext4-snapshot / 9p>; CPU pinning: <…>
- **Config:** <link to the config file used, or an inline TOML block>
- **`kiln doctor` output:** <collapsed block>
- **Reproduce with:**

```bash
(wsl) $ ./scripts/dev.sh lab <id> …
```

## 5. Method

<!-- Fill in every item of the spec/10 §9 checklist: experiments/variants, interpreters, warm-ups/repeats/interleaving,
     source location, metric definitions, comparison method + practical-significance threshold (chosen BEFORE looking),
     record paths. -->

## 6. Results: before

| Variant | Metric | Median | 95% CI | n | Record |
|---|---|---|---|---|---|
| | | | | | `results/published/Kn/...` |

<!-- Charts: link or embed SVGs generated from the records. -->

## 7. The fix

<!-- What changed (link to the commit or diff) and WHY it should help, in terms of the mechanism explained in the primers. -->

## 8. Results: after

| Variant | Metric | Median | 95% CI | n | Record |
|---|---|---|---|---|---|

**Comparison:** ratio of medians after/before = <x> (95% CI <a>–<b>) → <interpretation per spec/10 §4.3>.

## 9. Analysis

<!-- Was the hypothesis right? Why or why not? What surprised us? Tie each observation back to the mechanism. -->

## 10. Reference comparison

<!-- How <reference implementation> handles this, with links to SPECIFIC files/functions/lines (pinned to a tag or commit).
     What did they do differently, and why might they have chosen that? What would we change in Kiln after reading it? -->

## 11. Limitations and threats to validity

<!-- WSL/9P, laptop noise, sample size, synthetic workload vs. real app, version differences, anything that could mislead. -->

## 12. What you should now be able to explain

- [ ] <Explain …> (research §3 area: …)
- [ ] …

## 13. Checkpoint quiz

1. <Question>
   <details><summary>Answer</summary>

   <Answer.>

   </details>
