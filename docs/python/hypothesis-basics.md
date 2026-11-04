# Hypothesis basics (property-based testing)

> **First used in:** see the [ledger](README.md) (milestone K1) · **Needs:** [pytest-basics](pytest-basics.md), [decorators](decorators.md), [classes-and-objects](classes-and-objects.md) · **Reading time:** ~10 min
> **Terms introduced:** **property**, **strategy**, **@given**, **shrinking**, **falsifying example**, **stateful testing**, **rule / invariant**

## What it looks like

```python
from hypothesis import given, strategies as st

@given(st.lists(st.integers()))
def test_sorting_keeps_every_item(numbers):
    assert sorted(numbers) == sorted(sorted(numbers))      # sorting twice changes nothing
```

## What it means

A normal test checks **one example** you picked: `assert sort([3, 1]) == [1, 3]`. A **property**-based test states a rule that must hold for **every** input, and the Hypothesis library generates the inputs, by default 100 different ones per test run.

| Piece | Meaning |
|---|---|
| **strategy** | a recipe for generating inputs: `st.integers()`, `st.text()`, `st.binary()`, `st.lists(st.integers(), min_size=1)`, `st.dictionaries(st.text(), st.integers())`... |
| **@given**(strategy) | a decorator that runs the test many times, passing a freshly generated value each time |
| **shrinking** | when a generated input breaks the test, Hypothesis keeps simplifying it (shorter lists, smaller numbers) while the test still fails, then reports the simplest version |
| **falsifying example** | that simplest failing input, printed with the failure |

Hypothesis also favours "nasty" values on purpose: empty lists, `0`, huge numbers, unusual Unicode. These are the cases people forget to test by hand.

### Stateful testing

**Stateful testing** goes one step further. Instead of one function call, Hypothesis generates whole *sequences of operations* against an object, using a `RuleBasedStateMachine`:

- each method decorated with `@rule()` is one possible operation (push, pop, submit, cancel...). Hypothesis picks random rules in random orders;
- each method decorated with `@invariant()` is a fact that must hold **after every step**;
- the usual trick is to drive the real object and a simple **model** (plain lists and dicts) side by side, and check that they always agree.

When a sequence fails, it's shrunk too, down to the shortest list of steps that breaks the invariant.

## Why people use it

Example tests only find the bugs you already thought of. Properties find the ones you didn't. The demo below has a `mean()` function whose test looks fine, until Hypothesis tries an empty list. Kiln has two places where "every possible input" matters a lot:
- the frame decoder must work no matter how the bytes are split into pieces;
- the job table must never break its state-machine rules, whatever order events arrive in.

## How Kiln uses it

- [`tests/unit/test_framing.py`](../../tests/unit/test_framing.py) generates random JSON objects and random split points. Its property: decoding the frames, fed in any number of pieces, always returns exactly the original objects (AC-1.1).
- [`tests/unit/test_jobtable_state_machine.py`](../../tests/unit/test_jobtable_state_machine.py) is a `RuleBasedStateMachine`. Its rules are submit, dispatch, result, timeout, worker lost, cancel, stale result and evict. Six invariants run after every step (K1 spec §6.1), for example "a terminal job never changes state".

## Gotchas

- **Write the property, not the answer.** "sorted output is in order and has the same items" works for any input. Re-implementing the function inside the test just copies its bugs.
- **Read the falsifying example first.** It's the smallest case that fails, often an empty list or `0`, and usually points straight at the bug.
- **Hypothesis remembers failures.** It saves failing examples in a `.hypothesis/` folder and tries them first next time (git-ignored in Kiln).
- **Default: 100 examples.** Use `@settings(max_examples=500)` for more (AC-1.2 asks for ≥ 500 for the job table), or `derandomize=True` for the same inputs every run.

## Try it

[`snippets/hypothesis_demo_example.py`](snippets/hypothesis_demo_example.py) contains a buggy `mean()` (it divides by `len(numbers)`), two property tests for it, and a small state machine comparing a `deque` with a plain list. Running it with pytest from the Kiln venv:

```bash
(wsl) $ cd /tmp/kiln-hyp && ~/.cache/kiln/venvs/py3.15/bin/python -m pytest hypothesis_demo_example.py -p no:cacheprovider
```

```text
collected 3 items

hypothesis_demo_example.py F..                                           [100%]

=================================== FAILURES ===================================
_______________________ test_mean_is_between_min_and_max _______________________
…
numbers = []

    @given(st.lists(st.integers()))
    @settings(derandomize=True)
    def test_mean_is_between_min_and_max(numbers):
        """For any list of integers, the mean lies between the smallest and largest value."""
>       assert min(numbers) <= mean(numbers) <= max(numbers)
               ^^^^^^^^^^^^
E       ValueError: min() iterable argument is empty
E       Failing test case: test_mean_is_between_min_and_max(
E           numbers=[],
E       )
…
========================= 1 failed, 2 passed in 0.65s ==========================
```

(Real output, trimmed, with Hypothesis 6.170.14 and pytest 9.2.0 on Python 3.15.1. The file was copied to `/tmp/kiln-hyp` so the repo's own pytest settings don't apply.)

What happened:
- Hypothesis found the **falsifying example** `numbers=[]`, an empty list. Even the *test's* `min(numbers)` crashes on it, before `mean` gets its chance to divide by zero. Both the function and the property need a rule for empty input.
- The second test asks for `min_size=1` and passes.
- The state machine, a `deque` against a list model through random pushes and pops, passes too.

You can also call a `@given` test directly. [`snippets/hypothesis_basics_1.py`](snippets/hypothesis_basics_1.py) checks that JSON round-trips random dictionaries:

```text
property held; examples tried: True
an empty dict was among them: True
```

## Check yourself

1. What's the difference between a strategy and a property?
   <details><summary>Answer</summary>

   A strategy describes *what inputs to generate* (for example lists of integers). A property is the rule the test asserts for every one of those inputs (for example "the mean is between the min and the max").

   </details>
2. Hypothesis first failed on `[-3, 7, 0, 12, -1]` but reported `[]`. Why?
   <details><summary>Answer</summary>

   Shrinking: after finding a failure, Hypothesis keeps simplifying the input while it still fails, and reports the simplest one. A smaller example is much easier to debug.

   </details>
3. In a `RuleBasedStateMachine`, what's the difference between a `@rule` and an `@invariant`?
   <details><summary>Answer</summary>

   A rule is an operation Hypothesis may perform, in any order and any number of times (submit a job, cancel a job...). An invariant is a check that runs after every operation and must always pass (for example "the queue contains exactly the QUEUED jobs").

   </details>
