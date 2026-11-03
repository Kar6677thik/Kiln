# Documentation Standard (binding for every implementation round)

> **Who this is for:** whoever writes Kiln code, which means the implementer ([ADR-001](../decisions.md#adr-001)).
> **Why it exists:** the learner is **new to Python and new to systems programming** ([ADR-002](../decisions.md#adr-002)). The learner said: *"just the docs should rectify all my doubts."* So in this repo, documentation **is the product**. Code without its docs is unfinished work.
> **Enforcement:** `scripts/check_docs.py` (built in K0) checks everything marked 🤖 below. A human (the learner) judges everything marked 👤. The milestone isn't done until both pass ([ADR-026](../decisions.md#adr-026)).

---

## 0. The golden rules

1. **Assume nothing.** If a term isn't in the glossary and hasn't been explained earlier in the reading order, explain it where it first appears, or link to where it's explained.
2. **Why before how.** Every piece of documentation, from a comment to a primer, first says *why the thing exists* (what goes wrong without it), and only then *how it works*.
3. **Concrete before abstract.** Show an example, a command with real output, or a diagram, *then* generalize.
4. **One idea per paragraph. Short sentences.** Prefer tables and lists over long prose.
5. **Every claim is checkable.** A claim about behaviour ("fork copies fds") comes with a command or test the reader can run to see it.
6. **Unclear means bug.** If the learner asks something the docs didn't answer, the answer goes **into the docs** (and into `docs/questions.md` → answered), not only into a chat reply.
7. **Plain Python is preferred.** If a simpler construct does the job, use it. If a clever construct is really better, keep it and explain it (`# WHY:` + `# PY:`).

## 1. The seven kinds of documentation

| # | Kind | Location | Answers | Written when |
|---|---|---|---|---|
| 1 | **Spec** | `docs/spec/` | *What must the system do?* | spec pass (done); updated by ADR |
| 2 | **Concept primer** | `docs/concepts/<slug>.md` | *What is a fork / signal / GC... and why should I care?* | in the milestone that first needs it, **before** the code that uses it |
| 3 | **Python note** | `docs/python/<slug>.md` | *What does this Python syntax or feature mean?* | the first time the feature appears in `src/`, `labs/` or `tests/` |
| 4 | **In-code docs** | docstrings + comments | *What does this line / function / module do, and why?* | with the code, never later |
| 5 | **Code tour** | `docs/tours/Kn.md` | *In which order should I read the code, and what should I notice?* | at the end of each milestone |
| 6 | **Lab report** | `docs/labs/Kn-<slug>.md` | *What did we expect, what did we measure, what did we learn?* | during and after the milestone's experiment |
| 7 | **Glossary / FAQ / questions** | `docs/glossary.md`, `docs/faq.md`, `docs/questions.md` | *What does this word mean? What do people usually get confused about?* | continuously |

## 2. Concept primers (`docs/concepts/`)

Template: [`../templates/concept-primer.md`](../templates/concept-primer.md). Each primer **must** have these sections, in this order 🤖:

1. **One-sentence summary**, the kind you'd say to a friend, followed by the **Terms introduced** line, which lists every new term in bold. Each one must also appear in the glossary 🤖.
2. **Why you should care.** The real-world incident or bug this concept explains, linked to research §2 where possible.
3. **The mental model.** An analogy plus a diagram (ASCII *and* optionally Mermaid). The analogy must be labelled as an analogy, and the primer must say where it breaks down.
4. **See it for yourself.** At least one runnable snippet (stored in `docs/concepts/snippets/<slug>_*.py` so tests can run it 🤖), with the **exact command** and the **real output**, captured from a real run and not invented.
5. **How it really works.** The precise explanation, at the level of the OS, CPython or the protocol. Name the syscalls and the CPython source file or function where relevant.
6. **How Kiln uses it.** Links to the exact modules and functions.
7. **Common misconceptions.** At least 2, each as "❌ Myth: … ✅ Reality: …".
8. **Check yourself.** 3–6 questions, with answers inside `<details><summary>Answer</summary>…</details>`.
9. **Go deeper.** 2–5 links: official docs, man pages, PEPs, the CPython source.

Length guide: **600–2,500 words**. If a concept needs more, split it.

**Required primer list:** each milestone spec lists the primers it must produce (in its "Docs deliverables" section). `docs/concepts/README.md` is the index, with reading order and status.

## 3. Python notes (`docs/python/`)

Purpose: the learner can read *any* line in the repo. Template: [`../templates/python-note.md`](../templates/python-note.md).

- **One note per feature**, e.g. `decorators.md`, `dataclasses.md`, `context-managers.md`, `type-hints.md`, `bytes-vs-str.md`, `enum.md`, `exceptions.md`, `generators.md`, `f-strings.md`, `t-strings.md`, `async-await.md`, `walrus.md`, `match-statement.md`, `slots.md`, `protocols.md`, `comprehensions.md`.
- Each note has: **What it looks like** (a tiny example) → **What it means** (a line-by-line explanation) → **Why people use it** → **How Kiln uses it** (links) → **Gotchas** → **Try it** (a REPL session with real output) → **Check yourself**.
- **The ledger:** `docs/python/README.md` is a table of every Python feature used in the repo, with columns `feature | note | first used in (file:line) | milestone`. 🤖 `check_docs.py` scans the AST of `src/`, `labs/`, `tests/` and `scripts/*.py` for constructs in a known list (decorators, `with`, `async def`, `yield`, comprehensions, `match`, `:=`, `lambda`, `*args/**kwargs`, f-strings, t-strings, `dataclass`, `Enum`, `Protocol`, `TypeVar`/PEP 695 generics, `__slots__`, dunder methods, `try/except/finally`, `global`/`nonlocal`, star-unpacking, slicing, `assert`, `del`) and fails if a construct appears with no ledger row.
- K0 writes the **starter set** before any K1 code. See the K0 spec.

## 4. In-code documentation

### 4.1 Module docstring (required in every `.py` file 🤖)

```python
"""Master-side job table: the queue, every job's record, and result expiry.

WHY THIS MODULE EXISTS
    The master must remember every job it has accepted: which are waiting, which are running
    where, and which have finished, so it can answer `status`/`wait`, retry lost jobs and
    evict old results. Keeping all of that in one class with one set of rules (the state
    machine in docs/spec/03-jobs-and-tasks.md §4) keeps those rules in one place.

HOW IT FITS
    arbiter.py  ──calls──▶  JobTable.submit / .start / .finish / .lose
    dispatcher.py ─reads──▶ JobTable.queue

READ FIRST
    docs/spec/03-jobs-and-tasks.md, docs/concepts/state-machines.md, docs/python/dataclasses.md

MILESTONE
    Introduced in K1. Changed in: (list later milestones as they touch it)
"""
```

Required headings 🤖: `WHY THIS MODULE EXISTS`, `HOW IT FITS`, `READ FIRST`, `MILESTONE`.

### 4.2 Function, method and class docstrings (every function, including private ones 🤖)

Google style, with Kiln's extra sections:

```python
def classify_exit(handle: WorkerHandle, wait_status: int) -> Death:
    """Turn the raw number from waitpid() into a human-meaningful cause of death.

    Why:
        waitpid() packs "exited with code N" and "killed by signal S" into one integer.
        Telling them apart is how we tell "the task called sys.exit(1)" apart from
        "the kernel OOM killer SIGKILLed us" (research §2.7, the Back Market incident).

    Args:
        handle: the master's record of the worker, used to check whether *we* sent the kill.
        wait_status: the second value returned by os.waitpid().

    Returns:
        A Death describing how the worker ended and what to do with its in-flight jobs.

    Example:
        >>> classify_exit(h, 9)              # 9 = "terminated by signal 9" (SIGKILL)
        Death(how='killed', signal='SIGKILL', suspected_oom=True, ...)

    See also:
        docs/concepts/exit-status.md, docs/spec/04-supervisor.md §4.1
    """
```

- `Why:` is **required** 🤖 on every function longer than 5 lines and on every class.
- `Example:` is required on public functions, and doctests are run by pytest where feasible.
- Trivial one-liners may use a single-line docstring, but it must still say *why* when that isn't obvious.

### 4.3 Comment tags (grep-able, checked 🤖)

| Tag | Use it for | Example |
|---|---|---|
| `# WHY:` | the reason for a non-obvious line or design choice | `# WHY: non-blocking so one slow client can't freeze the master (ADR-006)` |
| `# PY:` | explaining a Python construct, **at its first use in each file** | `# PY: "with" calls __enter__ now and __exit__ when the block ends, even on error. See docs/python/context-managers.md` |
| `# CONCEPT(<slug>):` | linking to the primer that explains what's happening | `# CONCEPT(fork): after this line there are TWO processes running this same code` |
| `# GOTCHA:` | a trap that has bitten real people | `# GOTCHA: the child inherited the master's wakeup fd; reset it or signals go to the wrong process` |
| `# INCIDENT(<id>):` | linking code to the real incident it reproduces or prevents | `# INCIDENT(instagram-cow): this is the gc.freeze() recipe from research §2.1` |
| `# SYSCALL:` | naming the OS call underneath a Python call | `# SYSCALL: os.waitpid → wait4(2); see 'man 2 wait'` |
| `# INVARIANT:` | a fact that must always hold here | `# INVARIANT: a terminal job never changes state again` |
| `# LAB:` | code that exists only to demonstrate a problem, which must **never** be copied into production | `# LAB: deliberately leaks; see docs/labs/K6-leaks.md` |

### 4.4 How dense should comments be?

This is a learning repo, so the normal advice ("don't over-comment") is **deliberately overridden**:

- **Systems code** (`master/`, `worker/`, `launch/`, `memory/`, `gateway/`): expect roughly **one explanatory comment per 1–4 lines of code**. Every syscall, every fd operation, every signal-related line and every place where ordering matters gets a comment.
- **Plain logic** (`config.py`, `cli.py`, `bench/report/`): one comment per logical block is fine.
- **Comments explain behaviour and intent, not syntax that a Python note already covers.** ❌ `i += 1  # add one to i` ✅ `jobs_done += 1  # WHY: max_jobs recycling (spec 04 §5.5) counts from here`
- **No stale comments.** Changing code means updating its comments in the same commit. 👤 The reviewer checks this.

### 4.5 Tests are documentation too

- Every test function has a docstring 🤖 with **what behaviour** it pins down and **why that behaviour matters** (link to the spec section).
- Test names read like sentences: `test_worker_killed_by_external_sigkill_is_reported_as_suspected_oom`.
- Integration tests print a short narrative when run with `-s`, so the learner can *watch* a scenario happen (`KILN_TEST_NARRATE=1`).

### 4.6 Error messages and logs teach too

- Every user-facing error says **what happened, why it's a problem, and what to do**:
  `config error: worker.max_rss_mb must be ≥ 0 (got -5). Set it to 0 to disable RSS recycling.`
- Every log event has a stable `event=` name, documented in [`../spec/06-observability.md`](../spec/06-observability.md).

## 5. Code tours (`docs/tours/Kn.md`)

Template: [`../templates/code-tour.md`](../templates/code-tour.md). It's a guided reading path through the milestone's code:

1. **Before you start**: the primers to read first.
2. **The map**: a diagram of the files touched and how they call each other.
3. **Stops 1…N**: each stop is `path/to/file.py:L10-L48`, plus *what to look at*, *what question this code answers*, and *what would break without it*.
4. **Watch it run**: commands that exercise the code with logging or narration turned on, plus the expected output.
5. **Change it and see**: 2–4 small experiments the learner can try. *"Comment out line X and run test Y. It fails because…"*
6. **Checkpoint quiz**: 5–10 questions with hidden answers.

## 6. Lab reports (`docs/labs/Kn-<slug>.md`)

Template: [`../templates/lab-report.md`](../templates/lab-report.md). Required sections 🤖:

1. **The incident**: a retelling of the research §2 story in plain words.
2. **Your prediction**: an *empty* box for the learner, which the implementer never fills in. The implementer writes a separate **Implementer's hypothesis** with its reasoning.
3. **Verified facts**: a table for every version-specific claim the lab relies on: claim → command → observed → ✅/❌ ([ADR-020](../decisions.md#adr-020)).
4. **Setup**: the exact interpreter(s), commit hash, config, machine notes (WSL, CPU count, RAM), and the commands to reproduce.
5. **Method**: what is varied, what is measured, how many repeats, and why that's enough ([spec/10](../spec/10-measurement-methodology.md)).
6. **Results: before**: tables and charts with real numbers, linked to `results/published/...` records.
7. **The fix**: what changed and why it should help.
8. **Results: after**: same format as before.
9. **Analysis**: was the hypothesis right? Why or why not? What surprised us?
10. **Reference comparison**: how the reference implementation (gunicorn, Celery, aiodebug, Memray...) handles this, linked to *specific* source files and lines, *what they did differently and why*.
11. **Limitations and threats to validity**: what could make these numbers misleading (WSL, 9P, a noisy laptop...).
12. **What you should now be able to explain**: a checklist tied to the knowledge map (research §3).
13. **Checkpoint quiz**.

**Numbers are never invented or "illustrative".** If a measurement wasn't taken, the cell says `not measured`, with the reason.

## 7. Glossary, FAQ, questions

- `docs/glossary.md`: every technical term used anywhere in docs or comments. Format: **Term**: a plain-English definition in 1–3 sentences, then *"Example: …"*, then *"See: link"*. Alphabetical. 🤖 The checker extracts `**bold**` terms from the "terms introduced" list in each primer and fails if any of them is missing from the glossary.
- `docs/faq.md`: questions people commonly have ("Why not just use Celery?", "Why does RSS add up to more than my RAM?").
- `docs/questions.md`: the learner's open questions. Each is answered by linking to where the docs now explain it, then marked ✅.

## 8. Diagrams

- **ASCII diagrams are required** for every architecture or sequence explanation, because they render everywhere, including terminals and code comments.
- Mermaid is **optional** in addition, inside ```` ```mermaid ```` blocks.
- Sequence diagrams use the vertical timeline style of [spec/01 §6](../spec/01-architecture.md#6-data-flow-the-life-of-one-job-happy-path).

## 9. Writing style

- Second person ("you"), active voice, present tense.
- Define acronyms on first use per document: "copy-on-write (CoW)".
- Every code block names its language. Every shell block shows the prompt context (`(wsl) $`) and the **real** output.
- Bold the term being defined. Don't bold for emphasis elsewhere.
- Prefer "for example" to "e.g." in prose. (Tables may abbreviate.)
- Never say "simply", "just", "obviously" or "trivially". If it were obvious, the learner wouldn't be reading.

## 10. What `scripts/check_docs.py` checks (🤖 summary)

| Check | Fails when |
|---|---|
| Module docstrings | a `.py` under `src/`, `labs/`, `scripts/` lacks a docstring or one of the 4 headings |
| Function docstrings | a function or class lacks a docstring; a function > 5 lines lacks `Why:` |
| Python ledger | an AST construct from the known list has no ledger row |
| Comment tags | `CONCEPT(x)` or `INCIDENT(x)` references a slug with no primer or incident entry |
| Links | any relative markdown link or `docs/...` path in a comment points to a missing file or anchor |
| Primer sections | a primer is missing a required heading, or has no `<details>` answers |
| Snippets | a snippet listed in a primer doesn't exist or fails when run (`pytest -m snippets`) |
| Lab report sections | a required heading is missing; a results table cell is empty (it must say `not measured` with a reason) |
| Glossary | a term listed in a primer's "Terms introduced" is missing from the glossary |
| Tests | a test function lacks a docstring |
| Milestone deliverables | a file listed in a milestone's "Docs deliverables" doesn't exist |
