<!--
TEMPLATE: concept primer. Copy to docs/concepts/<slug>.md. Rules: docs/standards/documentation.md §2.
- Keep the headings EXACTLY as written (scripts/check_docs.py looks for them).
- 600–2,500 words. If it's longer, split it into two primers.
- Assume the reader is new to Python and to systems programming. Explain or link every term.
- Every output shown must come from a REAL run of a snippet in docs/concepts/snippets/.
Delete these comments when done.
-->

# <Concept name>

> **Milestone:** Kn · **Read before:** <primers that should be read first, as links> · **Reading time:** ~N min
> **Terms introduced:** **term one**, **term two**, **term three** <!-- each must also be in docs/glossary.md -->

## One-sentence summary

<!-- The kind of sentence you'd say to a friend. No jargon, or jargon that is immediately explained. -->

## Why you should care

<!-- The real bug or incident this concept explains. Link to research §2.x and the lab report.
     "Without understanding this, you'll be surprised when…" -->

## The mental model

<!-- 1) An analogy, clearly labelled "Analogy:". 2) Where the analogy breaks down ("Where the analogy fails:").
     3) An ASCII diagram (required). Mermaid is optional, in addition. -->

```
<ASCII diagram>
```

## See it for yourself

<!-- At least one runnable snippet. Store it in docs/concepts/snippets/<slug>_<n>.py (header comment: "# requires: linux, py>=3.x" if needed).
     Show the exact command and the REAL output. Put the normalized expected output in <name>.expected.txt (spec/09 §7). -->

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/<slug>_1.py
```

```text
<real output>
```

<!-- Then explain the output line by line. -->

## How it really works

<!-- The precise explanation: OS / CPython / protocol level. Name syscalls (with "man 2 <name>"),
     CPython source files or functions (e.g. Modules/gcmodule.c, Python/gc.c), and PEPs. -->

## How Kiln uses it

<!-- Links to the exact modules and functions: [`master/supervisor.py` → `classify_exit`](../../src/kiln/master/supervisor.py).
     Which comment tag points here: CONCEPT(<slug>). -->

## Common misconceptions

- ❌ **Myth:** …
  ✅ **Reality:** …
- ❌ **Myth:** …
  ✅ **Reality:** …

## Check yourself

1. <Question>
   <details><summary>Answer</summary>

   <Answer, with the reasoning, not just the result.>

   </details>
2. …
<!-- 3–6 questions. At least one "predict what this prints" question. -->

## Go deeper

- <Official docs / man page / PEP / CPython source link>: one line on what you'll find there
- …
