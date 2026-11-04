# Reading /proc/<pid>/smaps_rollup

> **Milestone:** K3 · **Read before:** [memory-metrics](memory-metrics.md), [copy-on-write](copy-on-write.md) · **Reading time:** ~12 min
> **Terms introduced:** **smaps_rollup**, **PSS**, **USS**, **Shared_Clean / Shared_Dirty**, **Private_Clean / Private_Dirty**

## One-sentence summary

`/proc/<pid>/smaps_rollup` is a text file the kernel generates on demand that adds up, over all of a process's memory, how many resident pages are shared or private and clean or dirty, plus the fair-share total **PSS**, which is the only per-process number you can safely add up across processes.

## Why you should care

"How much memory does each worker use?" has at least three honest answers, and they differ by hundreds of MiB once workers share a preloaded app. Kiln's recycling (`worker.memory_metric = pss | uss`, spec/04 §5.5), the `kiln mem` command and every K3 measurement read this file. If you add up the wrong column, 4 workers sharing a 300 MiB graph look like 1.2 GiB when the machine really spent about 300 MiB.

## The mental model

**Analogy:** a shared flat. **RSS** is "how many rooms can I walk into". **PSS** is "my share of the rent": a room shared by 4 flatmates costs each a quarter. **USS** is "the rooms that would become empty if I moved out". Adding everybody's RSS counts the shared kitchen four times. Adding everybody's PSS gives exactly the whole flat.

**Where the analogy fails:** rooms change hands silently. When one flatmate writes on the kitchen wall, copy-on-write gives them a *private* kitchen: their USS grows, and the shared kitchen is now split among fewer people, so everyone else's PSS goes up a little too.

```
  2 processes, 40 MiB of data shared after fork (each also has a little private memory)

            shared pages (counted once in RAM)         private pages
  parent  [■■■■■■■■■■■■■■■■■■■■ 40 MiB ■■■■■■■■■■■■■■] [▪ ~0.7]
  child   [same frames                              ] [▪ ~0.5]

  RSS  = shared + private          → parent ≈ 52, child ≈ 45  (each counts all 40+ shared)
  PSS  = shared / sharers + private → parent ≈ 24.5, child ≈ 22.5
  USS  = private only               → parent ≈ 0.7, child ≈ 0.5
  Σ RSS ≈ 97 MiB (wrong total)       Σ PSS ≈ 47 MiB (the real total)
```

## See it for yourself

[`snippets/smaps_1.py`](snippets/smaps_1.py) fills 40 MiB with random bytes, forks a child that just waits, and reads both processes' `smaps_rollup`:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/smaps_1.py
```

```text
one raw 'Rss:' line: Rss:               52968 kB
parent Rss   52.0  Pss   24.5  USS    0.7  Shared   51.3  (MiB)
child  Rss   45.4  Pss   22.5  USS    0.5  Shared   44.9  (MiB)
sum of RSS =   97.4 MiB   sum of PSS =   47.0 MiB
```

(Python 3.15.1.)

- The raw line says **kB**, but the kernel means KiB (1024 bytes). Kiln converts with `× 1024` and a `# GOTCHA:` comment.
- Each process's **Rss** includes the whole shared 40 MiB plus the interpreter's own shared pages, so the two RSS values add up to 97.4 MiB.
- **Pss** charges each shared page half to each process: 24.5 + 22.5 = 47.0 MiB, which is what the two processes really occupy.
- **USS** is under 1 MiB in both: right after fork almost nothing is private.
- Not shown above: of the parent's 51.3 MiB "Shared", a separate run measured 43.1 MiB as `Shared_Dirty` and 8.6 MiB as `Shared_Clean`. The 40 MiB buffer was *written by the parent before the fork*, so its pages are dirty and shared. After the child was killed, the parent's PSS rose from 24.4 to 46.3 MiB and those pages turned into `Private_Dirty` (43.4 MiB), with no change in the parent itself.

Here is a complete file, from a fresh Python 3.15.1 process (no fork):

```text
001ff000-7ffcad9ee000 ---p 00000000 00:00 0                              [rollup]
Rss:               33700 kB
Pss:               27526 kB
Pss_Dirty:         24668 kB
Pss_Anon:          24668 kB
Pss_File:           2858 kB
Pss_Shmem:             0 kB
Shared_Clean:       8804 kB
Shared_Dirty:          0 kB
Private_Clean:       228 kB
Private_Dirty:     24668 kB
Referenced:        33700 kB
Anonymous:         24668 kB
KSM:                   0 kB
LazyFree:              0 kB
AnonHugePages:         0 kB
ShmemPmdMapped:        0 kB
FilePmdMapped:         0 kB
Shared_Hugetlb:        0 kB
Private_Hugetlb:       0 kB
Swap:                  0 kB
SwapPss:               0 kB
Locked:                0 kB
```

## How it really works

The kernel walks every mapping of the process (the same list `/proc/<pid>/smaps` shows one by one) and, for each resident page, asks how many processes map it. Field by field:

| Field | Meaning | What to use it for |
|---|---|---|
| first line `[rollup]` | the address range covered: all mappings | nothing |
| `Rss` | every resident page, shared or not | "how much can this process touch right now"; over-counts in sums |
| `Pss` | each page divided by the number of processes mapping it | **the fair share; Σ PSS = real total** |
| `Pss_Anon` / `Pss_File` / `Pss_Shmem` | PSS split by kind: heap-like anonymous memory, file-backed (code, libraries), shared memory | K3 focuses on `Pss_Anon`: the Python objects. `Pss_File` is mostly the interpreter binary and `.so` files |
| `Pss_Dirty` | PSS of dirty pages only | (newer kernels) |
| `Shared_Clean` | pages mapped by more than one process, never written since loading or forking | the "still shared" memory |
| `Shared_Dirty` | shared pages that were modified *before* they became shared | e.g. heap written by the parent before fork |
| `Private_Clean` | pages only this process maps, unmodified (often library pages nobody else uses) | part of USS |
| `Private_Dirty` | pages only this process maps, modified | **CoW copies show up here** |
| `Swap` / `SwapPss` | pages pushed to disk | should be 0, or timing data is void |
| `AnonHugePages` | memory backed by 2 MiB huge pages | 0 here: THP is `madvise` on this machine |

**Derived numbers** ([spec/10 §7](../spec/10-measurement-methodology.md#7-memory-metrics-definitions)):
- **USS** (unique set size) = `Private_Clean + Private_Dirty`: what you'd get back if the process exited.
- **Shared** = `Shared_Clean + Shared_Dirty`.
- `Rss = Shared + USS` (the snippet's parent: 51.3 + 0.7 = 52.0).

**Cost.** Reading `smaps_rollup` makes the kernel walk the whole page table, which takes milliseconds for a big process. Kiln's single-threaded master reads it at most once every 5 s per worker (K3 spec §5.1), and labs read it from a separate sampler process so the measurement doesn't disturb the measured.

**Permissions.** You can read your own processes' files. With `kernel.yama.ptrace_scope = 1` (this machine) a process may still read its *children's* files, which is how the master and `kiln mem --children-of` work (gate G3.5).

## How Kiln uses it

- [`src/kiln/memory/procfs.py`](../../src/kiln/memory/procfs.py): `read_smaps_rollup(pid)` returns a frozen `SmapsRollup` dataclass in bytes, with `uss` and `shared` properties; missing keys on older kernels become 0, and a vanished pid raises `ProcessGone`.
- `kiln mem PID... [--children-of PID]` prints RSS / PSS / USS / Shared / Private_Dirty / Swap per process and a Σ row, with a footnote that Σ RSS over-counts.
- `labs/k3/sampler.py` samples every 200 ms for the K3 time series.
- Code tag: `CONCEPT(smaps)`.

## Common misconceptions

- ❌ **Myth:** "USS is the same as Private_Dirty."
  ✅ **Reality:** USS also includes `Private_Clean` (for example library pages only this process happens to map). For CoW decay, `Private_Dirty` is the sharper signal; for "what would I free", use USS.
- ❌ **Myth:** "PSS is stable for a process that does nothing."
  ✅ **Reality:** PSS depends on *other* processes. If a sibling worker exits, the pages it shared are split among fewer processes and your PSS rises, without you doing anything.
- ❌ **Myth:** "`kB` means 1000 bytes."
  ✅ **Reality:** in `/proc`, `kB` means KiB (1024 bytes).

## Check yourself

1. Predict: in the snippet, the child exits. What happens to the parent's PSS, and its RSS?
   <details><summary>Answer</summary>

   PSS rises by about 20 MiB, to roughly its RSS: the 40 MiB is no longer split with anyone (measured: 24.4 → 46.3 MiB). RSS doesn't change (52.1 → 52.4 MiB), because the parent still has the same resident pages.

   </details>
2. Four workers each show RSS = 320 MiB, PSS = 90 MiB. How much memory do the workers really use together?
   <details><summary>Answer</summary>

   About 360 MiB (Σ PSS), plus whatever share is charged to the master if it maps the same pages. Σ RSS (1,280 MiB) counts the shared pages four times.

   </details>
3. Which field grows when a worker's shared memory is copied by CoW, and which one shrinks?
   <details><summary>Answer</summary>

   `Private_Dirty` grows in the worker that wrote. `Shared_Clean` shrinks in that worker (the page is no longer shared from its point of view).

   </details>
4. Why doesn't Kiln's master read `smaps_rollup` for every worker on every loop iteration?
   <details><summary>Answer</summary>

   Generating the file walks the target's whole page table, which can take milliseconds for a large process. The master must never block (ADR-006), so it reads at most every 5 s per worker.

   </details>

## Go deeper

- `man 5 proc`: `/proc/pid/smaps` and `/proc/pid/smaps_rollup`, every field.
- [Linux kernel docs: The /proc Filesystem, section "smaps"](https://docs.kernel.org/filesystems/proc.html).
- [spec/10 §7](../spec/10-measurement-methodology.md#7-memory-metrics-definitions): Kiln's definitions.
