# Virtual Memory and Pages

> **Milestone:** K3 · **Read before:** [what-is-a-process](what-is-a-process.md), [memory-metrics](memory-metrics.md), [fork](fork.md) · **Reading time:** ~12 min
> **Terms introduced:** **virtual address**, **physical memory**, **page table**, **page fault**, **minor fault**, **anonymous memory**

## One-sentence summary

Every process sees its own private, made-up map of addresses (**virtual addresses**); the kernel connects each 4 KiB **page** of that map to real RAM only when the page is first used, using a per-process **page table**.

## Why you should care

K3 is about memory that is *shared* between Kiln's master and its forked workers, and then quietly *stops* being shared (Instagram's problem, research [§2.1](../research/python-mastery-research.md#21-instagram-the-gc-breaks-copy-on-write-memory-sharing)). Sharing is only possible because addresses are virtual: two processes can use the *same* address to mean *different* memory, or *different* addresses to mean the *same* memory. Without this picture, "the worker is 300 MiB but the machine only lost 20 MiB" sounds impossible. With it, it's obvious.

## The mental model

**Analogy:** a library catalogue. Your program asks for "shelf 0x7f3a…". That's a catalogue number (a virtual address), not a physical location. Each process has its own catalogue (its page table). The librarian (the kernel and the CPU's memory unit) looks the number up and fetches the book from wherever it really is. If the catalogue entry is empty, the librarian stops you, finds an empty shelf, puts a blank book there, writes the entry, and lets you continue: that's a **page fault**.

**Where the analogy fails:** two catalogues can point at the *same* physical book. That's exactly how fork shares memory, and why the next primer ([copy-on-write](copy-on-write.md)) is needed: someone has to decide what happens when one reader scribbles in a shared book.

```
   process A (virtual)          page tables            physical RAM (4 KiB frames)
   ┌───────────────┐                                   ┌──────┐
   │ page 0x1000   │──────── A's table ──────────────▶ │ F7   │◀──┐  shared: both tables
   │ page 0x2000   │──────── A's table ─────┐          ├──────┤   │  point at frame F7
   │ page 0x3000   │  (no entry yet)        └────────▶ │ F2   │   │
   └───────────────┘                                   ├──────┤   │
   process B (virtual)                                 │ F9   │◀┐ │
   ┌───────────────┐                                   └──────┘ │ │
   │ page 0x1000   │──────── B's table ─────────────────────────┼─┘
   │ page 0x2000   │──────── B's table ─────────────────────────┘  same address, other frame
   └───────────────┘
   touching A's 0x3000 → page fault → kernel picks a free frame, zero-fills it, adds the entry
```

## See it for yourself

[`snippets/virtual-memory-and-pages_1.py`](snippets/virtual-memory-and-pages_1.py) reserves 64 MiB with `mmap`, then writes one byte in each page of the first half, counting the page faults the kernel reports (`resource.getrusage(...).ru_minflt`):

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/virtual-memory-and-pages_1.py
```

```text
page size: 4096 bytes; 64 MiB = 16384 pages
after reserving 64 MiB: page faults +0, RSS +0 MiB
after touching 32 MiB (8192 pages): page faults +8192, RSS +32 MiB
```

(Python 3.15.1, WSL2 kernel 6.6.87.2.)

- **page size 4096:** the unit of everything below. `getconf PAGESIZE` says the same.
- **reserving 64 MiB costs nothing:** zero faults, zero resident memory. The kernel only wrote "these addresses are allowed" into the process's map.
- **touching 8,192 pages caused exactly 8,192 page faults**, and RSS grew by exactly 32 MiB. One fault per page, on first touch. This is **lazy allocation**.

## How it really works

- **Virtual addresses.** Every pointer in a program (including every Python object's address, `id(obj)` in CPython) is virtual. The CPU's memory management unit (MMU) translates it on every access, using the current process's **page table**, a tree of entries kept by the kernel, one entry per mapped 4 KiB page. A cache called the TLB makes this fast.
- **What a page table entry holds:** the physical frame number, plus permission bits: present, writable, user-accessible, and others. These bits are how copy-on-write works: the kernel can mark a page *read-only* on purpose and catch the first write.
- **Page faults.** Touching a page whose entry isn't present traps into the kernel (`man 2 mmap`, `man 5 proc`):
  - a **minor fault** is solved without disk: zero-fill a fresh frame (new **anonymous memory**), or map a page already in RAM (a shared library someone else loaded, a page in the file cache);
  - a **major fault** needs disk I/O (reading a file or swap), which is far slower.
- **Anonymous vs file-backed memory.** Memory from `malloc` and Python's object allocator is **anonymous memory**: it isn't backed by any file, and starts as zeros. (Python's `mmap.mmap(-1, n)`, used in the snippet, is a *shared* anonymous mapping: the kernel counts it as shared memory, `RssShmem`, rather than `RssAnon`.) Code and shared libraries are *file-backed*: their pages can be re-read from disk, so the kernel can drop clean copies under memory pressure. In `/proc/<pid>/smaps_rollup` these appear separately as `Pss_Anon` and `Pss_File` ([smaps](smaps.md)).
- **What `fork()` copies.** Fork does **not** copy RAM. It copies the parent's *page tables*: the child gets a new table whose entries point at the *same* physical frames, and both sets of writable entries are marked read-only. That's why forking a 300 MiB master takes milliseconds (K2 measured 5–7 ms per worker), and why the child starts out sharing everything ([copy-on-write](copy-on-write.md)).
- **Huge pages.** Some kernels can back memory with 2 MiB pages (Transparent Huge Pages, THP). On this machine `/sys/kernel/mm/transparent_hugepage/enabled` is `always [madvise] never`: only memory that asks for it with `madvise` gets huge pages, so Kiln's measurements use ordinary 4 KiB pages. If THP were `always`, one write could copy 2 MiB instead of 4 KiB. The K3 lab report records this setting as a threat to validity.

## How Kiln uses it

- Kiln never manages pages itself. It *reads* the kernel's accounting: [`src/kiln/memory/procfs.py`](../../src/kiln/memory/procfs.py) parses `/proc/<pid>/status` (K1) and, from K3, `/proc/<pid>/smaps_rollup` (`read_smaps_rollup`, returning a `SmapsRollup`).
- The K3 preload graph (`src/kiln/labs/preload.py`) is built in the master *before* fork, so its pages start shared; the lab then watches which pages each worker copies.
- Code tag: `CONCEPT(virtual-memory-and-pages)`.

## Common misconceptions

- ❌ **Myth:** "`id(obj)` is where the object physically sits in RAM."
  ✅ **Reality:** it's a virtual address. Two forked workers print the same `id()` for an object they both inherited, while possibly holding it in different physical frames.
- ❌ **Myth:** "allocating a big buffer immediately uses that much RAM."
  ✅ **Reality:** pages are given RAM on first touch. The snippet reserved 64 MiB and RSS didn't move.
- ❌ **Myth:** "fork copies the parent's memory, so it's slow for big processes."
  ✅ **Reality:** it copies page *tables*, not pages. The copies happen later, one page at a time, only for pages someone writes to.

## Check yourself

1. Predict: a Python program does `area = mmap.mmap(-1, 256 * 2**20)` and then only *reads* one byte from each page. Does RSS grow?
   <details><summary>Answer</summary>

   Yes, by the full 256 MiB (measured on 3.15.1). Python's `mmap.mmap(-1, n)` creates a *shared* anonymous mapping (backed by the kernel's shared-memory filesystem), and the first access to each page, read or write, gives it a real page. "Reserving is free" holds only until you touch the pages, by any kind of access.

   </details>
2. Why did touching 8,192 pages cause exactly 8,192 page faults, not one per byte?
   <details><summary>Answer</summary>

   Faults happen per page, on the first access. After the kernel maps a page, every other byte in that 4 KiB page is accessed without the kernel's involvement.

   </details>
3. After `fork()`, the child prints the same `id()` as the parent for an object. Are they using the same RAM?
   <details><summary>Answer</summary>

   At first, yes: both page tables point at the same frames. The same virtual address is a coincidence of copying the map. As soon as either one writes to that page, it gets its own copy at the same virtual address but a different physical frame.

   </details>
4. What's the difference between a minor and a major page fault?
   <details><summary>Answer</summary>

   A minor fault is solved in memory (zero-fill a new frame, or map a page already in RAM). A major fault has to read from disk, which takes far longer.

   </details>

## Go deeper

- `man 2 mmap`, `man 2 fork`, `man 5 proc` (`/proc/pid/smaps`, `/proc/pid/stat` minflt): the kernel's own descriptions.
- [Linux kernel docs: Memory Management Concepts](https://docs.kernel.org/admin-guide/mm/concepts.html): pages, page tables, huge pages, anonymous memory.
- [Linux kernel docs: Transparent Hugepage Support](https://docs.kernel.org/admin-guide/mm/transhuge.html): what `always/madvise/never` mean.
- Next: [copy-on-write](copy-on-write.md).
