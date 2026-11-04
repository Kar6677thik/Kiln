# Memory Metrics: Virtual Size, RSS and the High-Water Mark

> **Milestone:** K1 (K3 adds PSS and USS) · **Read before:** [what-is-a-process](what-is-a-process.md) · **Reading time:** ~12 min
> **Terms introduced:** **virtual memory**, **page**, **resident memory**, **RSS**, **VmSize**, **VmHWM**, **page fault**

## One-sentence summary

A process can *reserve* far more memory than it *uses*. **RSS** counts what is really sitting in RAM right now, and **VmHWM** remembers the highest RSS the process ever reached.

## Why you should care

K1's worker recycling (`worker.max_rss_mb`, [spec/04 §5.5](../spec/04-supervisor.md#55-recycling-max_jobs-max_rss)) and the OOM story (research [§2.7](../research/python-mastery-research.md#27-celery-and-pre-fork-workers-memory-multiplied-by-concurrency-secondary-sources), [cgroups-and-oom-killer](cgroups-and-oom-killer.md)) are both about memory. If you read the wrong number, you'll restart workers that are fine, or miss the one about to be killed. Wikimedia's "each forked child grew to about 310 MB" is an RSS number. K3 then shows why RSS, summed over workers, *over-counts* (Instagram's copy-on-write story).

## The mental model

**Analogy:** a hotel booking. **Virtual memory** is the rooms you've *reserved*. **Resident memory** is the rooms where someone is actually sleeping tonight. The hotel (the kernel) only gives you a real room the first time you walk in. Reserving 200 rooms costs the hotel almost nothing until guests arrive.

**Where the analogy fails:** two processes can share the *same* physical room (shared libraries, and pages after `fork()`), and RSS counts that room in *both* bills. That's the K3 lesson.

```
 virtual address space (VmSize): what the process MAY touch
 ┌──────────────────────────────────────────────────────────────┐
 │ code │ heap ███████░░░░░░░░ │ mmap area ██████░░░░░░░░░░░░░░ │   █ = page in RAM (counted in RSS)
 └──────────────────────────────────────────────────────────────┘   ░ = reserved, never touched
          RSS (VmRSS) = all the █ pages      VmHWM = the most █ ever at once
```

## See it for yourself

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/memory-metrics_1.py
```

```text
start                              VmSize=   40 MiB  VmRSS=  11 MiB  VmHWM=  11 MiB
after reserving 200 MiB            VmSize=  240 MiB  VmRSS=  11 MiB  VmHWM=  11 MiB
after touching 100 MiB of it       VmSize=  240 MiB  VmRSS= 111 MiB  VmHWM= 111 MiB
after releasing it                 VmSize=   40 MiB  VmRSS=  11 MiB  VmHWM= 111 MiB
```

Line by line:
- **start:** a fresh Python is already 40 MiB *virtual* but only 11 MiB *resident*.
- **reserving 200 MiB** (`mmap`) raises VmSize by exactly 200, while RSS doesn't move. Nothing has been touched yet.
- **touching 100 MiB**, one byte per 4 KiB **page**, makes the kernel hand out a real RAM page on each first touch. RSS rises by 100 MiB, and so does VmHWM.
- **releasing it** brings VmSize and RSS back down, but **VmHWM stays at 111**. It's a high-water mark, like the line a flood leaves on a wall.

## How it really works

- Memory is managed in **pages**, normally 4 KiB. A process's addresses are *virtual*. The kernel maps each virtual page to a physical page of RAM only when the page is first used.
- That first use triggers a **page fault**: the CPU traps into the kernel, which finds a free physical page, fills it with zeros, maps it, and resumes the program. This lazy approach is why reserving is cheap.
- `/proc/<pid>/status` reports these numbers in kB (really KiB):
  - **VmSize:** the total virtual size.
  - **VmRSS:** the resident set size, meaning pages currently in RAM. It splits into `RssAnon` (heap and anonymous memory), `RssFile` (memory-mapped files such as libraries) and `RssShmem`.
  - **VmHWM:** the peak RSS since the process started.
- **Why RSS over-counts:** a shared library, or a page shared after `fork()`, is resident *once* in RAM but appears in *every* sharing process's RSS. Add up the RSS of 4 workers and you can exceed the machine's real usage. K3 introduces **PSS** (shared pages divided fairly) and **USS** (only private pages), read from `/proc/<pid>/smaps_rollup`.
- **Python and memory:** CPython requests memory in big blocks (arenas) and reuses it internally. RSS doesn't always shrink when Python objects are freed. A "leak" is RSS that grows *without bound*, not RSS that stays high once (K6).

## How Kiln uses it

- [`memory/procfs.py` → `read_status`](../../src/kiln/memory/procfs.py) parses `/proc/<pid>/status` into bytes: `vm_rss`, `vm_hwm`, `rss_anon`, `rss_file`, `rss_shmem`.
- Workers send `rss_bytes` in every heartbeat. After each job they compare RSS with `worker.max_rss_mb` and retire themselves gracefully if it's over (spec/04 §5.5). The master double-checks from the heartbeats, because a stuck worker can't check itself.
- `kiln doctor` D14 checks `smaps_rollup` is readable, for K3.

## Common misconceptions

- ❌ **Myth:** "VmSize is how much memory my program uses."
  ✅ **Reality:** it's how much it has *reserved*. RSS is what's in RAM. Programs routinely reserve gigabytes and use megabytes.
- ❌ **Myth:** "add up RSS over all processes to get total memory use."
  ✅ **Reality:** shared pages are counted once per process, so the sum over-counts, sometimes by a lot. K3's whole lab is about this.
- ❌ **Myth:** "when Python frees objects, RSS goes down."
  ✅ **Reality:** often it doesn't. CPython keeps freed memory to reuse it. Watch the *trend* over many jobs, not single drops.

## Check yourself

1. A process reserves 1 GiB with `mmap` and never touches it. Roughly how much does its RSS grow?
   <details><summary>Answer</summary>

   Almost nothing. Untouched pages are never given physical RAM, so only VmSize grows by 1 GiB.

   </details>
2. RSS went up to 500 MiB during a job and back to 100 MiB after. What do VmRSS and VmHWM show afterwards?
   <details><summary>Answer</summary>

   VmRSS ≈ 100 MiB (the current value). VmHWM ≈ 500 MiB (the peak, which never decreases).

   </details>
3. Predict: two forked workers share 200 MiB of their parent's pages without modifying them. What does each one's RSS include, and what's the real RAM used?
   <details><summary>Answer</summary>

   Each one's RSS includes the 200 MiB, so the sum shows 400 MiB or more. Physically the pages exist once, about 200 MiB. That's why K3 uses PSS.

   </details>
4. Why does Kiln check `max_rss_mb` in both the worker and the master?
   <details><summary>Answer</summary>

   The worker's check is cheap and exact, after each job. But a worker stuck inside a huge job can't check itself, so the master also watches the RSS reported in heartbeats (spec/04 §5.5).

   </details>

## Go deeper

- `man 5 proc`, sections `/proc/pid/status` and `/proc/pid/smaps`: the exact meaning of every field.
- [spec/10 §7](../spec/10-measurement-methodology.md#7-memory-metrics-definitions): Kiln's memory metric definitions.
- [K3 milestone](../milestones/K3-copy-on-write.md): PSS, USS and copy-on-write.
