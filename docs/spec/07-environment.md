# 07 — Environment: Setting Up the Machine Kiln Runs On

> **Status:** Approved spec. **Implemented in:** K0 (setup + `kiln doctor`), extended in K1 (cgroup), K5/K6 (ptrace) and K8 (Rust).
> **Concepts:** WSL2, Linux distribution, package manager (`apt`), `sudo`, shell, PATH, filesystem, kernel parameter (`sysctl`), cgroup. They're explained inline below, and the K0 primer `docs/concepts/wsl-and-linux-basics.md` goes deeper.

---

## 1. What we found on this machine (2026-11-03)

| Fact | Observed value | Why it matters |
|---|---|---|
| Host OS | Windows 11 Pro 10.0.26100 | Kiln can't run natively ([ADR-003](../decisions.md#adr-003)) |
| WSL distribution | **Debian 13 (trixie)**, WSL version 2, default distro | the research notes assumed Ubuntu; commands here are for Debian |
| WSL kernel | `6.6.87.2-microsoft-standard-WSL2` | modern: cgroup v2, `pidfd`, `posix_spawn` all available |
| Linux user | `Megatron` (home `/home/Megatron`) | paths below use `$HOME` so they work for any user |
| `uv` | installed at `~/.local/bin/uv` | manages Pythons and venvs |
| System Python | `/usr/bin/python3` 3.13.5 | **not** used for Kiln. uv-managed Pythons are used instead (§3) |
| `kernel.yama.ptrace_scope` | **1** | attach tools (Tachyon, `pdb -p`, `asyncio ps`) won't work by default (§5) |
| CPUs | 14 | the matrix benchmarks can pin to cores |
| RAM visible to WSL | ~7.5 GiB | sets the experiment budget ([ADR-023](../decisions.md#adr-023)) |
| C compiler (`gcc`) | **missing** | needed for K4's C extension and any sdist builds |
| Rust (`cargo`) | **missing** | needed for K8 |
| Docker Desktop | present (`docker-desktop` WSL distro) | not used by Kiln (the Cartographer project uses it) |

**What is WSL2?** The Windows Subsystem for Linux, version 2, runs a real Linux kernel in a lightweight virtual machine managed by Windows. Inside it you have a normal Linux system (here: Debian), with its own filesystem, users and processes. Windows files are visible from Linux under `/mnt/c/...`.

## 2. One-time system packages (K0, step 1)

Open the Debian shell (from Windows: `wsl -d Debian`) and run:

```bash
(wsl) $ sudo apt update
(wsl) $ sudo apt install -y build-essential gdb git rsync curl ca-certificates pkg-config \
                            procps util-linux strace ltrace libffi-dev
```

| Package | What it gives us | Needed by |
|---|---|---|
| `build-essential` | `gcc`, `make`, libc headers: a C compiler toolchain | K4 C extension, K8 (Rust uses `cc` as its linker), any package without a prebuilt wheel |
| `gdb` | the GNU debugger. It can attach to a running process and show C-level stacks | K3 (reproducing Instagram's "who called `gc.enable()`" GDB hunt, optional), K6 |
| `git` | version control *inside* WSL (Windows git also works on the same repo) | everything |
| `rsync` | efficient folder copy | the bench snapshot (§7) |
| `curl`, `ca-certificates` | downloading over HTTPS | rustup, manual HTTP tests against the gateway |
| `pkg-config` | helps build scripts find C libraries | some builds |
| `procps` | `ps`, `top`, `free`, `pmap`, `sysctl` | every lab |
| `util-linux` | `taskset` (CPU pinning), `prlimit`, `nsenter` | K9 noise control |
| `strace`, `ltrace` | print every syscall or library call a process makes | K1/K2 primers ("see it for yourself" sections) |
| `libffi-dev` | headers for ctypes and cffi builds | lab-only fallbacks |

`libssl-dev` is **not** needed. uv's Pythons bundle their own OpenSSL, and Kiln makes no TLS connections.

## 3. Python interpreters (K0, step 2)

```bash
(wsl) $ uv python install 3.13 3.14 3.14.4 3.14.5 3.15 3.15t
(wsl) $ uv python list --only-installed
```

| Interpreter | Role ([ADR-004](../decisions.md#adr-004)) |
|---|---|
| `3.15` (latest patch) | **the development interpreter**: what `scripts/dev.sh` uses unless told otherwise |
| `3.13`, `3.14` (latest patch) | test matrix (syntax floor 3.13) |
| `3.15t` | test matrix, **free-threaded build** (no GIL). The `t` stands for "threaded". `sys._is_gil_enabled()` returns `False` unless an extension turns the GIL back on |
| `3.14.4`, `3.14.5` | **K7 only**, pinned: the last version with the incremental GC vs. the first with the revert |

**Gate (K0):** if `uv python install 3.14.4` or `3.14.5` fails because that exact patch isn't in uv's index, record the error, install the closest available patches on each side of the GC change, and write an ADR updating K7. This is covered in K7's own gates too.

**Why uv's Pythons and not `apt install python3.x`?** Debian packages one Python version per release, and it patches it in ways that differ from upstream. uv downloads **python-build-standalone** builds, which are close to upstream CPython and can be installed side by side, any version, without `sudo`.

## 4. Where code lives vs. where environments live (ADR-003)

```
Windows:  C:\Users\Karthik Reddy\Documents\Study\Projects\Kiln          ← the repo (source of truth)
WSL:      /mnt/c/Users/Karthik Reddy/Documents/Study/Projects/Kiln      ← the SAME folder, seen from Linux (slow 9P mount)
WSL:      $HOME/.cache/kiln/venvs/<tag>/                                ← virtual environments (fast ext4)
WSL:      $HOME/kiln-bench/                                             ← rsync snapshot used for timing-sensitive benchmarks
```

**What is 9P, and why does it matter?** The `/mnt/c` folder isn't a normal Linux disk. Every file access is sent over a network-style protocol called **9P** to Windows, which does the real work. That makes `/mnt/c` roughly 5–20× slower than the Linux disk (ext4) for workloads made of many small files, and importing Python packages is exactly that kind of workload. It also handles symlinks and Unix permissions imperfectly.

**Rules:**
1. The virtualenv is created **on ext4**. `scripts/dev.sh` sets `UV_PROJECT_ENVIRONMENT="$HOME/.cache/kiln/venvs/py$TAG"`, where `$TAG` is something like `3.15` or `3.15t`, before calling `uv sync` or `uv run`.
2. Any experiment that measures **startup, import or spawn time** runs from `$HOME/kiln-bench/` (§7), never from `/mnt/c`. The lab report must state which location was used.
3. Memory experiments (RSS/PSS) are *not* affected by where the source lives, so they may run from `/mnt/c`.

## 5. ptrace and attaching to running processes (K5/K6)

**What is ptrace?** The Linux mechanism that lets one process read another's memory and registers. Debuggers (`gdb`), `strace`, Python's remote attach (`python -m pdb -p PID`, `sys.remote_exec`, PEP 768), `python -m asyncio ps PID` and the Tachyon sampling profiler (`python -m profiling.sampling attach PID`) all rely on it or on the related `process_vm_readv` call.

**Why it's restricted by default.** `kernel.yama.ptrace_scope = 1` means a process may only be traced by its *own ancestors* (the processes that started it, directly or indirectly). That stops malware running as you from reading your browser's memory. Our observed value is **1**, so `python -m pdb -p <worker pid>` from a second terminal fails with "Operation not permitted".

**Options, from most to least preferred:**

| Option | Command | Lasts until | Notes |
|---|---|---|---|
| A. Run the tool with sudo | `sudo -E "$(uv python find 3.15)" -m profiling.sampling attach PID` | that command | the tool must be the **same Python minor version and build** as the target (research §2.8) |
| B. Temporarily relax | `sudo sysctl kernel.yama.ptrace_scope=0` | the next WSL restart (`wsl --shutdown`) | **preferred for lab sessions**. Turn it back on with `=1` afterwards |
| C. Make it permanent | `echo 'kernel.yama.ptrace_scope = 0' \| sudo tee /etc/sysctl.d/10-ptrace.conf` | forever | **not recommended**. Documented only so the learner knows it exists |

`kiln doctor` reports the current value and whether attach will work, and prints option B as the fix.

## 6. cgroups and the OOM lab (K1)

**What is a cgroup?** A Linux "control group" is a bag of processes with shared resource limits, such as "together these processes may use at most 512 MiB of RAM". When the processes exceed `memory.max`, the kernel's **OOM killer** ("out of memory") picks a process in the group and kills it with **SIGKILL**. It can't be caught or ignored. This is the mechanism behind "container killed at its memory limit" (research §2.7), and SC-3 requires Kiln to detect it.

WSL2 uses **cgroup v2** (a single unified hierarchy at `/sys/fs/cgroup`). Check with:

```bash
(wsl) $ stat -fc %T /sys/fs/cgroup       # prints "cgroup2fs" for v2
(wsl) $ cat /proc/self/cgroup            # e.g. "0::/user.slice/..."
```

### 6.1 Preferred: systemd-run

If systemd is enabled in WSL, a one-off memory-limited scope is a single command:

```bash
(wsl) $ grep -A1 '\[boot\]' /etc/wsl.conf          # should show: systemd=true
(wsl) $ systemd-run --user --scope -p MemoryMax=512M -p MemorySwapMax=0 \
          ./scripts/dev.sh serve-demo --labs
```

- `--user --scope` runs the command in a new scope under your user's systemd manager.
- `MemorySwapMax=0` matters. Without it, the kernel may swap instead of killing, and the lab takes minutes instead of seconds.
- If `systemctl --user` reports that it "Failed to connect to bus", the user session bus isn't running. Fall back to §6.2.

**Enabling systemd (if `/etc/wsl.conf` lacks it):** add the following, then run `wsl --shutdown` from PowerShell and reopen Debian:

```ini
[boot]
systemd=true
```

That change affects the whole distro. The learner decides. The lab doesn't require it, because §6.2 always works.

### 6.2 Fallback: a manual cgroup with sudo

```bash
(wsl) $ sudo mkdir -p /sys/fs/cgroup/kiln-lab
(wsl) $ echo "+memory" | sudo tee /sys/fs/cgroup/cgroup.subtree_control   # may already be enabled
(wsl) $ echo 512M | sudo tee /sys/fs/cgroup/kiln-lab/memory.max
(wsl) $ echo 0    | sudo tee /sys/fs/cgroup/kiln-lab/memory.swap.max
(wsl) $ echo $$   | sudo tee /sys/fs/cgroup/kiln-lab/cgroup.procs            # move THIS shell into it
(wsl) $ ./scripts/dev.sh serve-demo --labs                                    # children inherit the cgroup
```

`scripts/dev.sh lab k1-oom` wraps both paths, trying §6.1 first and then §6.2. It also checks `memory.events` is readable, so that the master can confirm OOM kills (`oom_kill` counter, see [04-supervisor §4.2](04-supervisor.md#42-oom-evidence)).

**Gate (K1):** if neither path can produce a cgroup whose `memory.events` the master can read, SC-3 is judged on `suspected_oom=true` alone. That fallback is recorded in the lab report and in an errata entry.

## 7. The benchmark snapshot (ADR-003)

```bash
(wsl) $ ./scripts/dev.sh bench-snapshot
# = rsync -a --delete --exclude .git --exclude results "<repo on /mnt/c>/" "$HOME/kiln-bench/"
#   then: cd ~/kiln-bench && uv sync (venv also on ext4)
```

Timing labs then run as `cd ~/kiln-bench && ./scripts/dev.sh lab k9-matrix`. Results are written back to the repo's `results/` folder (the path is passed explicitly), so nothing gets lost when the snapshot is refreshed. Every ExperimentRecord stores the source location in `host.notes` (`"source=ext4-snapshot"` or `"source=9p"`).

## 8. RAM: `.wslconfig`

WSL2 takes a share of Windows' RAM, about 50% by default, which is why we see ~7.5 GiB. To change it, edit `C:\Users\Karthik Reddy\.wslconfig` on **Windows**:

```ini
[wsl2]
memory=10GB      # upper limit for the WSL VM
swap=0           # optional: makes OOM experiments faster and more predictable
```

Then run `wsl --shutdown` in PowerShell. **No experiment requires changing this**, because [ADR-023](../decisions.md#adr-023) caps experiments at 3 GiB. It's documented so the learner understands where the 7.5 GiB number comes from.

## 9. Rust toolchain (K8)

```bash
(wsl) $ curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
(wsl) $ source "$HOME/.cargo/env"
(wsl) $ rustc --version && cargo --version
(wsl) $ uv sync --group lab-k8           # installs maturin into the venv
```

- **rustup** installs and updates Rust. **cargo** is Rust's build tool and package manager. **maturin** builds a Rust crate into a Python wheel (a `.whl` file that pip and uv can install).
- The Rust linker uses `cc`, which comes from `build-essential` (§2).
- PyO3's support for the 3.15 free-threaded build and `abi3t` is checked in K8's verification gates, not here.

## 10. Running things from Windows (for the implementer)

The implementer's tools run on Windows. Every Kiln command goes through WSL:

```powershell
wsl.exe -d Debian --cd "/mnt/c/Users/Karthik Reddy/Documents/Study/Projects/Kiln" -- ./scripts/dev.sh check
```

**Quoting rules (the path has a space in "Karthik Reddy"):**
- Always quote the path, in PowerShell *and* in bash: `"$REPO/scripts/dev.sh"`, never `$REPO/scripts/dev.sh`.
- Inside scripts, compute the repo root as `REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"` and quote every use.
- Never build shell command strings by concatenation. In Python use `subprocess.run([...list...])`, which doesn't need quoting.
- `check_docs.py` and a test in K0 (`test_scripts_quote_paths`) run `shellcheck`-style checks: they fail on any unquoted `$REPO` / `$HOME` use in `scripts/*.sh`.
- Optional: install `shellcheck` with `sudo apt install shellcheck`, and `dev.sh lint` uses it when present.

## 11. Line endings

Windows tools like to write CRLF (`\r\n`) line endings. Bash scripts with CRLF fail with confusing errors like `$'\r': command not found`. The fix:

```gitattributes
* text=auto eol=lf
*.ps1 text eol=crlf
*.png binary
*.whl binary
```

`kiln doctor` checks that `scripts/dev.sh` contains no `\r`.

## 12. `kiln doctor` checks (K0)

`kiln doctor` prints a table: **check | status (✅ pass / ⚠️ warn / ❌ fail) | detail | fix**. `--json` prints the same as JSON. The exit code is 0 if there are no ❌, else 1. Some checks only matter for later milestones, so they show ⚠️ (not ❌) until that milestone's `--milestone Kn` flag is given.

| # | Check | How | Why | Fix hint | Severity |
|---|---|---|---|---|---|
| D1 | Running on Linux | `sys.platform == "linux"` | fork, `/proc`, signals | "run inside WSL: `wsl -d Debian`" | ❌ |
| D2 | Inside WSL2 | `"microsoft" in os.uname().release.lower()` | explains 9P/memory caveats in reports | info only | ✅/info |
| D3 | Python version ≥ 3.13 | `sys.version_info` | syntax floor (ADR-004) | `uv python install 3.15` | ❌ |
| D4 | All matrix interpreters installed | `uv python find <v>` for 3.13, 3.14, 3.15, 3.15t | test-matrix | `uv python install …` | ⚠️ |
| D5 | K7 interpreters installed | `uv python find 3.14.4` / `3.14.5` | K7 | as above, or see K7 gates | ⚠️ (❌ with `--milestone K7`) |
| D6 | Free-threaded build works | run `3.15t -c "import sys; print(sys._is_gil_enabled())"` → `False` | K4 | reinstall 3.15t | ⚠️ |
| D7 | Venv on ext4 | `UV_PROJECT_ENVIRONMENT` / `sys.prefix` doesn't start with `/mnt/` | 9P slowness (§4) | use `scripts/dev.sh` | ⚠️ |
| D8 | Source location | is the repo under `/mnt/`? | timing labs need the snapshot (§7) | `dev.sh bench-snapshot` | info |
| D9 | C compiler | `shutil.which("cc")` | K4, K8 | `sudo apt install build-essential` | ⚠️ (❌ with K4/K8) |
| D10 | Rust toolchain | `cargo --version` | K8 | §9 | ⚠️ (❌ with K8) |
| D11 | ptrace_scope | read `/proc/sys/kernel/yama/ptrace_scope` | attach tools | §5 option B | ⚠️ if ≥ 1 |
| D12 | cgroup v2 | `stat -f /sys/fs/cgroup` type `cgroup2fs` | OOM lab | — | ⚠️ |
| D13 | cgroup memory limit possible | `systemd-run --user --scope true` works, or `sudo -n true` works | OOM lab | §6 | ⚠️ (❌ with K1) |
| D14 | `/proc/self/smaps_rollup` readable | open and read | K3 memory metrics | — (needs kernel ≥ 4.14) | ❌ |
| D15 | CPU count and RAM | `os.cpu_count()`, `/proc/meminfo` `MemTotal` | experiment sizing (ADR-023) | `.wslconfig` (§8) | info, ⚠️ if RAM < 4 GiB |
| D16 | Line endings | no `\r` in `scripts/*.sh` | §11 | `git add --renormalize .` | ❌ |
| D17 | Tools present | `gdb`, `strace`, `rsync`, `taskset` | primers and labs | §2 | ⚠️ |
| D18 | Port 7340/7341 free | try to bind to `127.0.0.1` | `serve` | stop the other process | ⚠️ |
| D19 | Debug tools on this interpreter | `profiling.sampling` importable (3.15), `sys.remote_exec` exists (3.14+) | K5/K6 | use 3.15 | ⚠️ |
