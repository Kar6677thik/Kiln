<!--
TEMPLATE: Python note. Copy to docs/python/<slug>.md. Rules: docs/standards/documentation.md §3.
- One language feature per note. The reader may never have seen it before.
- Keep the headings EXACTLY as written (checked by scripts/check_docs.py).
- After writing it, add a row to the ledger in docs/python/README.md.
Delete these comments when done.
-->

# <Feature name> (e.g. "Decorators")

> **First used in:** `src/kiln/<file>.py:<line>` (milestone Kn) · **Needs:** <other Python notes to read first> · **Reading time:** ~N min
> **Terms introduced:** **term one**, **term two** <!-- each must also be in docs/glossary.md -->

## What it looks like

```python
# The smallest example that shows the feature, ideally ≤ 10 lines.
```

## What it means

<!-- Go through the example line by line. What does Python actually do, and in what order?
     If there's an "it's really just…" equivalent (e.g. `@d def f` == `f = d(f)`), show it. -->

## Why people use it

<!-- The problem it solves, and what code looked like without it. -->

## How Kiln uses it

<!-- Links to 1–3 real places in the repo, each with one sentence on why it's used there. -->

## Gotchas

- <A trap, with a tiny example of the wrong behaviour.>
- …

## Try it

<!-- A REPL session the reader can copy. REAL output only. Store longer examples in docs/python/snippets/. -->

```text
>>> …
…
```

## Check yourself

1. <Question>
   <details><summary>Answer</summary>

   <Answer with explanation.>

   </details>
<!-- 2–4 questions. -->
