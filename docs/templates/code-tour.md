<!--
TEMPLATE: code tour. Copy to docs/tours/Kn.md. Rules: docs/standards/documentation.md §5.
- A guided reading PATH: order matters. Start from the entry point, and follow the data or control flow.
- At least 5 stops. Line ranges must be exact at the commit named below; update them if the code moves.
Delete these comments when done.
-->

# Code Tour — Kn: <milestone name>

> **Commit:** `<hash>` · **Time needed:** ~N min · **Milestone spec:** [Kn](../milestones/Kn-<slug>.md) · **Lab report:** [Kn](../labs/Kn-<slug>.md)

## Before you start

Read these first (in order):
1. [<primer>](../concepts/<slug>.md): why: …
2. [<Python note>](../python/<slug>.md): why: …

## The map

<!-- ASCII diagram of the files touched in this milestone and who calls whom. -->

```
<diagram>
```

## Stop 1 — `<path/to/file.py>:L<start>-L<end>`

- **Look at:** <what to focus on>
- **This code answers the question:** "<e.g. how does the master know a worker died?>"
- **What would break without it:** <concrete consequence>
- **Notice:** <subtle details, comment tags worth reading>

## Stop 2 — …

<!-- … Stop N -->

## Watch it run

<!-- Commands that exercise this code with narration or logging on, plus REAL expected output (trimmed is fine; mark trims with "…"). -->

```bash
(wsl) $ KILN_TEST_NARRATE=1 ./scripts/dev.sh test tests/integration/test_<x>.py -s
```

```text
<real output>
```

## Change it and see

<!-- 2–4 small experiments. Each one: what to change, what to run, what you'll see, and WHY. -->

1. **Change:** comment out `<line>` in `<file>`. **Run:** `<command>`. **You'll see:** <failure>. **Because:** <explanation>. (Undo with `git checkout -- <file>`.)
2. …

## Checkpoint quiz

1. <Question>
   <details><summary>Answer</summary>

   <Answer.>

   </details>
<!-- 5–10 questions. Include the seed questions from the milestone spec §13. -->
