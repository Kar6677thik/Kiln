# Git Basics

> **Milestone:** K0 · **Read before:** [terminal-and-wsl](terminal-and-wsl.md) · **Reading time:** ~15 min
> **Terms introduced:** **git**, **repository**, **commit**, **branch**, **diff**, **merge**, **working tree**, **staging area**, **line endings**

## One-sentence summary

**Git** keeps a complete, labelled history of every saved version of the project, so you can see exactly what changed, when and why, and you can work on a milestone in a separate line of history (a **branch**) without disturbing the main one.

## Why you should care

Every Kiln milestone is built as a series of small, explained steps, and each step is one **commit** ([milestone-workflow §2](../standards/milestone-workflow.md#2-during-the-milestone-step-by-step)). When you review a milestone, you'll read its commits and **diffs** to see how the code grew. A commit message says *why* a change was made, which is often the most educational part.

Git is also involved in one classic cross-platform bug: invisible **line endings**. Windows tools write `\r\n` at the end of each line and Linux tools write `\n`. A shell script saved with `\r\n` breaks with a baffling error (see [terminal-and-wsl](terminal-and-wsl.md#see-it-for-yourself)). This repo uses git settings to make sure that never happens.

## The mental model

**Analogy:** git is like a photo album of your project. Each **commit** is a photo of every file at one moment, with a caption (the message) explaining what changed. A **branch** is a bookmark that says "the newest photo of *this* storyline is here". Taking a new photo moves the bookmark forward.

**Where the analogy fails:** git doesn't store full copies of unchanged files in every "photo". It stores each distinct file content once and reuses it, which is why history stays small. And unlike photos, commits can be **merged**: two storylines can be combined into one.

```
  working tree          staging area            repository (history)
  (files you edit)      ("next commit")         (commits, forever)
 ┌──────────────┐  git add  ┌────────────┐  git commit  ┌──────────────────────────────────┐
 │ hello.py (M) │ ────────▶ │ hello.py   │ ───────────▶ │ 65a3ea8 ── efca889               │
 └──────────────┘           └────────────┘              │   main      milestone/k0-demo ◀─ │
        ▲                                                │                         branch  │
        └────────── git switch / git restore ────────────┴──────────────────────────────────┘

 Kiln's milestone flow (ADR-025):

 main:              dcf741a (spec pass) ─────────────────────────────── merge ──▶
                          \                                           /
 milestone/k0-foundations: step 0.1 ── step 0.2 ── … ── step 0.7 ──▶ (learner signs off)
```

## See it for yourself

These commands were run for real in a throwaway repository (`/tmp/git-demo`) inside WSL. The `$ ...` lines are the commands, and everything else is git's actual output:

```text
$ echo "print(\"hello\")" > hello.py
$ git status --short
?? hello.py
$ git add hello.py
$ git commit -m "Add hello script"
65a3ea8 Add hello script
$ git switch -c milestone/k0-demo
Switched to a new branch 'milestone/k0-demo'
$ git diff
diff --git a/hello.py b/hello.py
index 11b15b1..f4e3d17 100644
--- a/hello.py
+++ b/hello.py
@@ -1 +1 @@
-print("hello")
+print("hello, kiln")
$ git log --oneline --all --graph
* efca889 Greet Kiln
* 65a3ea8 Add hello script
$ git switch main && git merge milestone/k0-demo
Updating 65a3ea8..efca889
Fast-forward
 hello.py | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
$ git branch
* main
  milestone/k0-demo
```

Reading it:
- `?? hello.py`: git sees a file it isn't tracking yet.
- `65a3ea8`: the start of the commit's id, a fingerprint (hash) of its contents. Yours will differ.
- In the `git diff` output, a line starting with `-` was removed and a line starting with `+` was added. `@@ -1 +1 @@` means "line 1 in the old version, line 1 in the new version".
- `Fast-forward`: `main` hadn't moved since the branch was created, so git only had to slide the `main` bookmark forward to the branch's newest commit.

And this is the real Kiln repository, seen from WSL at the start of K0:

```text
$ git log --oneline -3
dcf741a Spec pass: Kiln specs, ADRs, standards, templates and milestone plans K0-K10
$ git branch
  main
* milestone/k0-foundations
```

### Line endings, byte by byte

The snippet [`snippets/git-basics_1.py`](snippets/git-basics_1.py) shows the bytes behind "two lines of text":

```bash
(wsl) $ python3.15 docs/concepts/snippets/git-basics_1.py
```

```text
Unix (LF)      : b'first line\nsecond line\n'
Windows (CRLF) : b'first line\r\nsecond line\r\n'
lengths in bytes: 23 vs 25
program env is asked to find: 'bash\r'
```

The Windows version has two extra bytes: one `\r` (carriage return) per line. The last line shows what happens to a script's first line `#!/usr/bin/env bash` when it ends in `\r\n`. Linux cuts the line at `\n` only, so the program name becomes `bash\r`. That's exactly the error message in the terminal primer.

Git can show which files have which endings. Real output from the demo repo, after adding a file written with `\r\n`:

```text
$ git ls-files --eol crlf.sh hello.py
i/crlf  w/crlf  attr/                 	crlf.sh
i/lf    w/lf    attr/                 	hello.py
```

`i/` is the version stored in the repository (the **index**), `w/` is the copy in the working tree, and `attr/` shows any `.gitattributes` rule that applies (none in the throwaway demo).

## How it really works

**Three places a file can be:**

| Place | What it is | Commands |
|---|---|---|
| **Working tree** | the actual files in the folder, the ones you edit | `git status`, `git diff` |
| **Staging area** (also called the *index*) | the exact contents of the *next* commit | `git add`, `git diff --staged` |
| **Repository** | the permanent history of commits, in the hidden `.git/` folder | `git commit`, `git log`, `git show` |

**A commit** records a snapshot of all tracked files, the author, the time, a message, and a pointer to its **parent** commit(s). That chain of parents is the history. The commit's id is a hash computed from all of this, so changing anything in history would change every later id. That's what makes history trustworthy.

**A branch** is only a small file in `.git/` holding one commit id. `git commit` on a branch moves that pointer to the new commit. `git switch <name>` changes which branch you're "on" and updates the working tree to match it.

**A merge** combines two branches. If one already contains the other, git fast-forwards: it moves the pointer, as in the demo. Otherwise it creates a *merge commit* with two parents.

**Line endings and `.gitattributes`.** Windows git on this machine has `core.autocrlf=true` (real output of `git config --get core.autocrlf` in Windows: `true`). That setting converts `\n` to `\r\n` when files are checked out on Windows, which would break the shell scripts that WSL runs from the same folder. Kiln's [`.gitattributes`](../../.gitattributes) overrides it per file type:

```gitattributes
*       text=auto eol=lf     # every text file: store with LF, check out with LF
*.sh    text eol=lf          # shell scripts: always LF (belt and braces)
*.ps1   text eol=crlf        # PowerShell scripts are Windows-native: CRLF
*.png   binary               # never touch binary files
```

Because `.gitattributes` lives *inside* the repository, the rule travels with the code, whichever machine or git settings check it out. If a file ever ends up with the wrong endings, `git add --renormalize .` re-applies the rules.

**Kiln's milestone branch flow ([ADR-025](../decisions.md#adr-025)):**
1. Each milestone gets a branch named `milestone/k<N>-<slug>`, such as `milestone/k0-foundations`.
2. Each step in the milestone plan is **one commit**, whose message explains *what* changed and *why*.
3. `./scripts/dev.sh check` must be green before every commit.
4. When the milestone is done, you review it. Only after your sign-off is the branch merged into `main`.
5. There's no remote (GitHub etc.) yet. Nothing is pushed anywhere unless you ask.

## How Kiln uses it

- [`.gitattributes`](../../.gitattributes) forces LF endings, as explained above.
- [`src/kiln/bench/records.py`](../../src/kiln/bench/records.py) stores the current commit id and whether there were uncommitted changes (`"git": {"commit": "…", "dirty": false}`) in every experiment record ([spec/10 §2](../spec/10-measurement-methodology.md#2-the-experimentrecord-one-json-file-per-run)). A number in a lab report can then be traced back to the exact code that produced it.
- [`src/kiln/doctor.py`](../../src/kiln/doctor.py) check D16 verifies that `scripts/*.sh` contain no `\r`.
- The [milestone workflow](../standards/milestone-workflow.md) is built on branches and one commit per step.

## Common misconceptions

- ❌ **Myth:** a branch is a copy of all the files.
  ✅ **Reality:** a branch is a tiny pointer to one commit. Creating one is instant and costs almost nothing.
- ❌ **Myth:** `git add` saves your work.
  ✅ **Reality:** `git add` only *stages* the change, preparing it for the next commit. Nothing enters history until `git commit`.
- ❌ **Myth:** line endings are a cosmetic difference.
  ✅ **Reality:** `\r` is a real byte. To bash, `bash\r` is a different program name from `bash`, and the script fails with exit code 127.
- ❌ **Myth:** committing means publishing.
  ✅ **Reality:** commits are local until you `git push` to a remote. Kiln has no remote, and pushing needs your explicit OK.

## Check yourself

1. You edited `hello.py`, ran `git add hello.py`, then edited it again. What will `git commit` record?
   <details><summary>Answer</summary>

   The version from the moment you ran `git add`. The staging area holds a snapshot of the file at that time. The second edit stays in the working tree until you `git add` it again.

   </details>
2. In a `git diff`, what do lines starting with `-` and `+` mean?
   <details><summary>Answer</summary>

   `-` lines exist in the old version and were removed. `+` lines exist in the new version and were added. A changed line shows up as one `-` line followed by one `+` line.

   </details>
3. Why does Kiln's `.gitattributes` say `*.sh text eol=lf` even though `core.autocrlf` is `true` on Windows?
   <details><summary>Answer</summary>

   `autocrlf=true` would check shell scripts out with CRLF endings on Windows. WSL runs those same files with bash, which would then look for a program called `bash\r` and fail. The attribute file overrides the machine setting for this repository, so scripts always have LF.

   </details>
4. Predict the output of `git log --oneline` on `main` right after a fast-forward merge of a branch with 2 new commits.
   <details><summary>Answer</summary>

   It lists the branch's 2 new commits on top of the old `main` history, newest first. A fast-forward creates no extra merge commit. `main` now points at the branch's newest commit.

   </details>

## Go deeper

- [Pro Git book, chapters 1–3](https://git-scm.com/book/en/v2): free, the standard introduction (basics, branching).
- [`gitattributes` documentation](https://git-scm.com/docs/gitattributes), section "End-of-line conversion".
- [GitHub: Configuring Git to handle line endings](https://docs.github.com/en/get-started/getting-started-with-git/configuring-git-to-handle-line-endings).
- [ADR-025](../decisions.md#adr-025): Kiln's branch-per-milestone workflow.
