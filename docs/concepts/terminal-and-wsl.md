# The Terminal and WSL

> **Milestone:** K0 · **Read before:** nothing. This is the first primer. · **Reading time:** ~15 min
> **Terms introduced:** **terminal**, **shell**, **command**, **exit code**, **WSL2**, **9P**, **ext4**, **mount**, **quoting**

## One-sentence summary

A **terminal** is a window where you type **commands** for a program called a **shell** to run. **WSL2** is a real Linux computer living inside your Windows machine, and Kiln runs entirely inside it.

## Why you should care

Kiln is built on Linux-only features: `fork()` (copying a running program), Unix signals, the `/proc` folder and cgroups ([ADR-003](../decisions.md#adr-003)). Windows doesn't have them, so every Kiln command runs inside WSL, and you'll start almost every lab by typing a command.

Two things trip up nearly everyone working this way, and both will bite you if you don't know about them:

1. **Your folder name has a space in it** (`Karthik Reddy`). Shells split commands on spaces, so an unquoted path silently turns into two arguments.
2. **The same folder is reachable two ways.** `C:\Users\...` in Windows is `/mnt/c/Users/...` in Linux. The `/mnt/c` route is slow, and a Windows editor can write invisible `\r` characters that break shell scripts.

Without this primer, you'd hit errors like `cd: too many arguments` or `env: 'bash\r': No such file or directory` and have no idea why.

## The mental model

**Analogy:** think of a restaurant. The **terminal** is the order pad, the **shell** is the waiter who reads your order, and the **commands** are the cooks who do the actual work. The waiter reports back to you whether the dish worked: that's the **exit code**. WSL2 is a second kitchen, a Linux kitchen, built inside the same building as your Windows kitchen. A hatch between them (`/mnt/c`) lets each kitchen reach the other's pantry.

**Where the analogy fails:** a waiter takes one order and walks away. A shell can run many commands in sequence, connect one command's output to the next one's input, and make decisions based on exit codes. That makes it closer to a small programming language than to a waiter.

```
 ┌──────────────────────────── your Windows 11 PC ─────────────────────────────┐
 │                                                                               │
 │  Windows Terminal / PowerShell                                                │
 │      │  wsl -d Debian                                                         │
 │      ▼                                                                        │
 │  ┌────────────────── WSL2: a lightweight Linux virtual machine ───────────┐   │
 │  │  kernel 6.6.87.2-microsoft-standard-WSL2   (a REAL Linux kernel)        │   │
 │  │                                                                         │   │
 │  │  shell (zsh or bash) ──runs──▶ commands: ls, cd, python3.15, dev.sh ... │   │
 │  │                                                                         │   │
 │  │  /            ext4  ← Linux's own fast disk  (/home/Megatron, ~/.cache) │   │
 │  │  /mnt/c       9p    ← a window onto C:\  (slow; every access is         │   │
 │  │                       forwarded to Windows over the 9P protocol)        │   │
 │  └─────────────────────────────────────────────────────────────────────────┘   │
 │                                                                               │
 │  C:\Users\Karthik Reddy\Documents\Study\Projects\Kiln   ← the repo lives here │
 └───────────────────────────────────────────────────────────────────────────────┘
```

## See it for yourself

First, ask Python where it's running. The snippet is [`snippets/terminal-and-wsl_1.py`](snippets/terminal-and-wsl_1.py):

```bash
(wsl) $ cd "/mnt/c/Users/Karthik Reddy/Documents/Study/Projects/Kiln"
(wsl) $ python3.15 docs/concepts/snippets/terminal-and-wsl_1.py
```

```text
sys.platform     : linux
kernel release   : 6.6.87.2-microsoft-standard-WSL2
running in WSL?  : True
home folder      : /home/Megatron
current folder   : /mnt/c/Users/Karthik Reddy/Documents/Study/Projects/Kiln
current folder fs: 9p (mounted at /mnt/c)
home folder fs   : ext4 (mounted at /)
```

Line by line:
- `sys.platform: linux`: Python thinks it's on Linux, because it *is*.
- `kernel release ... microsoft-standard-WSL2`: the Linux kernel Microsoft builds for WSL2. `kiln doctor` uses exactly this string to detect WSL.
- `current folder fs: 9p`: the repo, seen through `/mnt/c`, lives on the slow 9P **mount**.
- `home folder fs: ext4`: your Linux home folder is on Linux's native disk. That's why Kiln's virtual environments live in `~/.cache/kiln/venvs/`.

`df -hT` shows the same two worlds (real output, trimmed):

```text
Filesystem     Type  Size  Used Avail Use% Mounted on
/dev/sdf       ext4 1007G  3.1G  953G   1% /
C:\            9p    476G  394G   83G  83% /mnt/c
```

### Exit codes: `$?`

Every command ends with a small number: **0 means success**, and anything else means some kind of failure. The shell keeps the last one in `$?`:

```text
$ ls no-such-file; echo "exit code: $?"
ls: cannot access 'no-such-file': No such file or directory
exit code: 2
$ ls README.md; echo "exit code: $?"
README.md
exit code: 0
```

`scripts/dev.sh` checks these numbers and stops at the first failure. Kiln's own `kiln doctor` exits with 1 when something is ❌.

### The space in your path

Real output from bash, with and without quotes:

```text
$ cd /mnt/c/Users/Karthik Reddy/Documents; echo "exit code: $?"
bash: cd: too many arguments
exit code: 1
$ cd "/mnt/c/Users/Karthik Reddy/Documents" && pwd
/mnt/c/Users/Karthik Reddy/Documents
```

Without quotes, bash splits the line at every space, so `cd` receives two arguments (`/mnt/c/Users/Karthik` and `Reddy/Documents`) and gives up. Your interactive shell is actually **zsh** (`echo $SHELL` prints `/usr/bin/zsh`). zsh fails differently but just as confusingly, with `zsh:cd:1: string not in pwd: /mnt/c/Users/Karthik`, because zsh treats `cd A B` as "replace A with B in the current path". The fix is the same in every shell: **quote paths**.

### Invisible `\r` characters

A script saved with Windows line endings (CRLF, `\r\n`) fails in a strange way. Real output:

```text
$ printf '#!/usr/bin/env bash\r\necho hello\r\n' > /tmp/crlf_demo.sh && chmod +x /tmp/crlf_demo.sh
$ /tmp/crlf_demo.sh; echo "exit code: $?"
env: 'bash\r': No such file or directory
exit code: 127
```

The first line asks `env` to find a program called `bash\r`, with an invisible carriage return on the end, and no such program exists. Exit code 127 means "command not found". The same script with Unix endings prints `hello`. The [git primer](git-basics.md) explains how the repo prevents this.

## How it really works

**The shell's job.** When you press Enter, the shell:
1. Splits the line into words, at spaces, unless they're quoted.
2. Expands variables like `$HOME` and `$?`.
3. Finds the program, by searching the folders listed in `$PATH`.
4. Starts it as a new **process** (see [what-is-a-process](what-is-a-process.md)), waits for it, and stores its exit code in `$?`.

`bash` and `zsh` are both shells. Kiln's scripts start with `#!/usr/bin/env bash`, so they always run under bash, whatever your interactive shell is.

**Quoting rules, in short:**

| You type | The program receives |
|---|---|
| `cd /mnt/c/Users/Karthik Reddy` | 2 arguments: `/mnt/c/Users/Karthik`, `Reddy` |
| `cd "/mnt/c/Users/Karthik Reddy"` | 1 argument, with the space inside |
| `echo "$HOME"` | 1 argument: your home path, with variables expanded inside `"…"` |
| `echo '$HOME'` | 1 argument: the literal text `$HOME`. No expansion inside `'…'` |

**What WSL2 is.** WSL2 runs a genuine Linux kernel in a small, managed virtual machine. Linux programs inside it make real Linux **system calls** (requests to the kernel), so `fork()`, signals and `/proc` all behave exactly as on a Linux server. Your WSL distribution is Debian 13, and the VM sees 14 CPUs and about 7.5 GiB of RAM (WSL's default is half of Windows' RAM; see [spec/07 §8](../spec/07-environment.md#8-ram-wslconfig)).

**`/mnt/c` and 9P.** Linux can't read the Windows disk directly. Instead, WSL **mounts** it at `/mnt/c` using the **9P** protocol. Every file open, read or `stat` becomes a message to a Windows-side server. One message is fast, but Python's `import` system touches *hundreds* of small files, which adds up to a slowdown of roughly 5–20× (spec/07 §4). The Linux root filesystem is **ext4**, a normal local disk image, and it's fast. That's why:
- the repo stays on `C:` (so Windows editors work), but
- the virtual environments are created in `~/.cache/kiln/venvs/py<tag>` on ext4, and
- timing experiments run from an ext4 copy (`~/kiln-bench/`, made by `dev.sh bench-snapshot`).

**Running Kiln commands from Windows.** PowerShell can ask WSL to run one command and return:

```powershell
wsl.exe -d Debian --cd "/mnt/c/Users/Karthik Reddy/Documents/Study/Projects/Kiln" -- ./scripts/dev.sh check
```

`-d Debian` picks the distribution, `--cd` sets the starting folder (quoted, because of the space), and everything after `--` is the command to run inside Linux.

## How Kiln uses it

- [`scripts/dev.sh`](../../scripts/dev.sh) is the single entry point for every task (`check`, `test`, `doctor`, …; see [spec/08 §3](../spec/08-repo-layout-and-tooling.md#3-scriptsdevsh-one-entry-point-for-everything)). It refuses to run outside Linux, quotes `"$REPO_ROOT"` everywhere because of the space, and exports `UV_PROJECT_ENVIRONMENT` so the venv lands on ext4.
- [`src/kiln/doctor.py`](../../src/kiln/doctor.py) (`kiln doctor`) checks that you're on Linux (D1), inside WSL2 (D2), that the venv isn't on `/mnt/` (D7), and that `scripts/*.sh` contain no `\r` characters (D16).
- [`src/kiln/bench/records.py`](../../src/kiln/bench/records.py) writes `source=9p` or `source=ext4-snapshot` into every experiment record, so a benchmark accidentally run on the slow mount is visible ([ADR-003](../decisions.md#adr-003)).

## Common misconceptions

- ❌ **Myth:** WSL is an emulator that pretends to be Linux.
  ✅ **Reality:** WSL2 runs a real Linux kernel. `uname -r` shows it, and `fork()`, signals and `/proc` are the genuine Linux implementations. (The older WSL1 *did* translate calls; we use WSL2.)
- ❌ **Myth:** `/mnt/c/...` and `C:\...` are copies of each other.
  ✅ **Reality:** they're the *same files*, seen through two doors. A change in one is instantly visible in the other. Only the speed and the line-ending habits of the tools differ.
- ❌ **Myth:** exit code 0 means "false", as in many programming languages.
  ✅ **Reality:** for processes, 0 means **success**. Non-zero codes are reserved for the many possible ways to fail.
- ❌ **Myth:** quotes are only needed for "weird" paths.
  ✅ **Reality:** in scripts, quote *every* variable (`"$REPO_ROOT"`). Today's path has a space, and tomorrow's might have a `*`.

## Check yourself

1. Predict what this prints, and why: `ls README.md missing.txt; echo $?` (assume `README.md` exists).
   <details><summary>Answer</summary>

   `ls` prints `README.md` and an error about `missing.txt`, then `echo` prints `2`. `ls` reports failure if *any* argument fails. The exit code describes the whole command, not each argument.

   </details>
2. Why does the Kiln venv live in `~/.cache/kiln/venvs/` instead of `.venv` inside the repo?
   <details><summary>Answer</summary>

   The repo is on `/mnt/c`, which WSL reaches through the slow 9P protocol, and a venv is thousands of small files that Python reads on every import. `~/.cache` is on ext4, Linux's native disk, which is much faster and handles symlinks and permissions properly.

   </details>
3. You run `./scripts/dev.sh check` and get `env: 'bash\r': No such file or directory`. What happened, and what would fix it?
   <details><summary>Answer</summary>

   The script has Windows (CRLF) line endings, so the first line asks for a program named `bash\r`. Fix it by converting the file to LF. In this repo, `.gitattributes` forces LF and `git add --renormalize .` re-applies it. `kiln doctor` check D16 detects it.

   </details>
4. What's the difference between `echo "$HOME"` and `echo '$HOME'`?
   <details><summary>Answer</summary>

   Double quotes keep the text as one argument *and* expand variables, so it prints `/home/Megatron`. Single quotes keep everything literal, so it prints `$HOME`.

   </details>

## Go deeper

- [Microsoft: Comparing WSL versions](https://learn.microsoft.com/windows/wsl/compare-versions): WSL1 vs WSL2, and the cross-filesystem performance note.
- [Microsoft: Working across file systems](https://learn.microsoft.com/windows/wsl/filesystems): why to keep Linux-heavy work on the Linux filesystem.
- `man bash`, section "QUOTING", for the exact rules for `'`, `"` and `\`.
- [The Missing Semester: The Shell](https://missing.csail.mit.edu/2020/course-shell/): a gentle hands-on shell course.
- [spec/07 Environment](../spec/07-environment.md): the full setup for this machine.
