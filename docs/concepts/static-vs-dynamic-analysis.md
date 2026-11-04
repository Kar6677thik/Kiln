# Static vs Dynamic Analysis: Reading Code vs Running It

> **Milestone:** K2 · **Read before:** [ast-and-symtable](ast-and-symtable.md) · **Reading time:** ~11 min
> **Terms introduced:** **static analysis**, **dynamic analysis**, **false positive**, **false negative**, **soundness**, **completeness**

## One-sentence summary

**Static analysis** inspects code without running it: it's fast and sees every path, but it guesses. **Dynamic analysis** runs the code and watches what happens: it's certain about what it saw, but sees only the paths that ran. A good tool uses each to cover the other's blind spots.

## Why you should care

`kiln forkcheck` reads Python files and *predicts* which ones will break under spawn or forkserver. Predictions can be wrong in two ways. A **false positive** warns about code that's fine, which wastes your time and teaches you to ignore the tool. A **false negative** stays silent about a real bug, which is the worse failure, because you trust the silence. K2 measures both, as precision and recall on a labelled corpus (K2 spec §5.11). It also adds `--confirm`, which *runs* the entry point under all three start methods, so a finding can be backed by observed behaviour.

## The mental model

**Analogy:** checking a recipe for allergens.
- Reading the ingredient list is static. It's fast and covers the whole recipe, but "spice mix" might hide peanuts (a false negative), and "may contain traces" warns about a dish that's fine (a false positive).
- Cooking it and testing the plate is dynamic. That's certain for *this* plate, but you only tested the version you cooked today.

**Where the analogy fails:** with code, the "cooking" can have side effects (it starts processes, writes files). That's why `--confirm` runs in subprocesses with a timeout.

```
                  actually breaks              actually fine
 flagged      │ true positive  (TP) ✔      │ false positive (FP) ✗ noise
 not flagged  │ false negative (FN) ✗ miss │ true negative  (TN) ✔

 precision = TP / (TP + FP)   "when it warns, how often is it right?"
 recall    = TP / (TP + FN)   "of the real bugs, how many does it catch?"
```

## See it for yourself

[`snippets/static-vs-dynamic-analysis_1.py`](snippets/static-vs-dynamic-analysis_1.py) runs a deliberately naive static checker: "flag `Process(target=<lambda>)`". It's tried on three cases:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/static-vs-dynamic-analysis_1.py
```

```text
a: lambda target (real bug)            flagged: True
b: lambda via a variable (real bug)    flagged: False
c: lambda under fork context (works)   flagged: True
```

- **Case a** is a true positive.
- **Case b** is a **false negative**: the lambda was stored in a variable first, and the naive check only looks at the call.
- **Case c** is a **false positive**: an explicit `fork` context never pickles the target, so it works.

forkcheck's real FA002 resolves names to their definitions, which fixes case b. It downgrades explicit-fork sites to `info`, which handles case c. But every rule has *some* cases like these, and the calibration corpus is how we find out which.

## How it really works

- **Soundness** (no false negatives: every real bug is reported) and **completeness** (no false positives: everything reported is real) can't both be fully achieved for interesting properties of real programs. Rice's theorem says, roughly, that non-trivial questions about what a program does are undecidable. Every practical checker is therefore a set of *heuristics* tuned on examples.
- **Precision vs recall trade-off.** A stricter rule (fewer warnings) raises precision and lowers recall. A looser one does the reverse. forkcheck's severities express the trade-off. `error` is reserved for patterns that will fail. `warning` covers likely problems (FA003/FA004 are the most heuristic). `info` means "works, but relies on fork".
- **Dynamic confirmation** answers "does behaviour actually change?" by running the entry point under fork, forkserver and spawn and comparing exit status and normalised output (K2 spec §5.9). It can *confirm* that something differs, but it can't say *which* finding caused it, and it sees only the paths this run took. Static and dynamic together beat either alone: static finds candidates everywhere, dynamic proves the important ones.

## How Kiln uses it

- [`src/kiln/forkcheck/rules.py`](../../src/kiln/forkcheck/rules.py): the static side (FA001–FA008), each rule documenting its known false positives.
- [`src/kiln/forkcheck/confirm.py`](../../src/kiln/forkcheck/confirm.py): the dynamic side (`--confirm`).
- [`../../corpus/real/`](../../corpus/real/) and `corpus/synthetic/`: labelled examples (`must_fire`, `must_not_fire`, `pos_`/`neg_` files) used to measure precision and recall in the K2 lab.

## Common misconceptions

- ❌ **Myth:** "A checker with no warnings means my code is fine."
  ✅ **Reality:** silence can be a false negative. Recall below 1.0 means some real bugs go unreported.
- ❌ **Myth:** "If a test passed, the code is correct."
  ✅ **Reality:** dynamic analysis covers only the paths that ran. A test under fork says nothing about spawn.
- ❌ **Myth:** "More warnings means a better checker."
  ✅ **Reality:** low precision trains people to ignore warnings, which is how real bugs get through.

## Check yourself

1. A rule flags 10 lines. 8 are real bugs, and the corpus has 2 more real bugs it missed. What are its precision and recall?
   <details><summary>Answer</summary>

   Precision = 8 / (8 + 2) = 0.8, since 2 of the 10 flags were false positives. Recall = 8 / (8 + 2) = 0.8, since 2 real bugs were missed.

   </details>
2. A rule has precision 1.0 and recall 0.5. What does that mean in words?
   <details><summary>Answer</summary>

   Every warning it gives is a real problem, but it only finds half of the real problems. Trust its warnings, but don't trust its silence.

   </details>
3. Why does `--confirm` alone not replace the static rules?
   <details><summary>Answer</summary>

   It only sees what one run of one entry point does. It can't point to the line that caused a difference, and it can't see code paths that didn't run. The static rules point at specific lines everywhere, and confirmation checks that the behaviour really changes.

   </details>

## Go deeper

- [Wikipedia: Precision and recall](https://en.wikipedia.org/wiki/Precision_and_recall).
- [Wikipedia: Rice's theorem](https://en.wikipedia.org/wiki/Rice%27s_theorem).
- [vLLM's multiprocessing design notes](https://docs.vllm.ai/en/latest/design/multiprocessing.html): a team that chose runtime checks over static detection.
