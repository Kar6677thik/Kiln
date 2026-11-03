# pathlib (file paths as objects)

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [classes and objects](classes-and-objects.md), [bytes vs. str](bytes-vs-str.md) · **Reading time:** ~9 min
> **Terms introduced:** **path**, **Path object**, **absolute path**, **relative path**, **PosixPath**

## What it looks like

```python
from pathlib import Path

p = Path("/tmp") / "kiln-pathlib-demo" / "notes.txt"
p.parent.mkdir(exist_ok=True)
p.write_text("hello\n", encoding="utf-8")
text = p.read_text(encoding="utf-8")
```

## What it means

A **path** is the address of a file or folder, like `/tmp/kiln-pathlib-demo/notes.txt`. On Linux, folders are separated by `/`. A path starting with `/` is an **absolute path** (it starts from the top of the filesystem). One without is a **relative path** (it's relative to the current folder).

`pathlib.Path` turns a path into a **Path object**: a value with useful methods, instead of a plain string.

- `Path("/tmp")` creates one. On Linux it's printed as a **PosixPath** ("POSIX" is the family of Unix standards Linux follows).
- The `/` operator **joins** paths: `Path("/tmp") / "notes.txt"`. It inserts exactly one separator, so you never end up with missing or doubled slashes.
- Parts of the path: `.name` (`notes.txt`), `.suffix` (`.txt`), `.stem` (`notes`), `.parent` (the containing folder, again a Path).
- Questions: `.exists()`, `.is_dir()`, `.is_file()`.
- Actions: `.read_text(encoding=...)`, `.write_text(...)`, `.mkdir(...)`, `.iterdir()` (loop over a folder's entries), `.read_bytes()` / `.write_bytes()` for raw data.
- `str(p)` gives the plain string back, for the rare function that needs one.

## Why people use it

Paths built by gluing strings break easily: `"/tmp" + "kiln-pathlib-demo"` silently gives `/tmpkiln-pathlib-demo` (see "Try it"). The older `os.path` functions work, but they're spread across modules (`os.path.join`, `os.path.exists`, `open(...)`). A `Path` keeps everything about a path in one object, it reads naturally, and type checkers know it's a path and not some random string. Kiln's ruff configuration even enables the `PTH` rules, which flag old-style `os.path` code (spec/08 §2.3).

## How Kiln uses it

- [`src/kiln/doctor.py`](../../src/kiln/doctor.py) reads Linux's special files with `Path`. Examples are `/proc/sys/kernel/yama/ptrace_scope` and `/proc/meminfo`, and checking `/sys/fs/cgroup`. Its `DoctorContext` stores `proc_root` and `sys_root` as `Path` values, so tests can point them at a fake folder tree under `tmp_path` (K0 spec §5.6).
- [`src/kiln/bench/records.py`](../../src/kiln/bench/records.py) builds `results/<milestone>/<experiment>/<run_id>.json` by joining with `/`, creates the folders with `mkdir(parents=True, exist_ok=True)`, and writes the file.
- [`scripts/check_docs.py`](../../scripts/check_docs.py) walks the `docs/` and `src/` trees to find files to check.

## Gotchas

- **Always pass `encoding="utf-8"`** to `read_text`/`write_text`. Without it, Python picks a default that depended on the computer's settings in older versions. (Python 3.15 defaults to UTF-8, PEP 686, but Kiln supports 3.13, and being explicit costs nothing.)
- **`mkdir` fails if the folder exists**, unless you pass `exist_ok=True`. It also fails if the parent folders are missing, unless you pass `parents=True`.
- **`/` with an absolute right side throws away the left side.** `Path("/tmp") / "/etc"` is `/etc`. That surprises people, and it matters if the right side comes from user input.
- **A Path is only an address.** Creating `Path("x.txt")` doesn't create or check a file. Only methods like `.exists()` or `.read_text()` touch the disk.
- **Relative paths depend on the current folder** of the process, which can differ between your terminal, a test and a worker. Kiln code builds paths from known roots instead.
- **`/proc` files are generated on the fly** by the Linux kernel (the `files-and-file-descriptors` primer explains this). Reading them works like reading any file, but their contents change from one read to the next.

## Try it

```text
>>> from pathlib import Path
>>> folder = Path("/tmp") / "kiln-pathlib-demo"
>>> p = folder / "notes.txt"
>>> p
PosixPath('/tmp/kiln-pathlib-demo/notes.txt')
>>> p.name, p.suffix, p.stem
('notes.txt', '.txt', 'notes')
>>> p.parent
PosixPath('/tmp/kiln-pathlib-demo')
>>> folder.mkdir(exist_ok=True)
>>> p.write_text("hello\n", encoding="utf-8")
6
>>> p.read_text(encoding="utf-8")
'hello\n'
>>> p.exists()
True
>>> (folder / "missing.txt").exists()
False
>>> Path("/proc/self/status").read_text().splitlines()[0]
'Name:\tpython3.15'
>>> str(p)
'/tmp/kiln-pathlib-demo/notes.txt'
>>> "/tmp" + "kiln-pathlib-demo"
'/tmpkiln-pathlib-demo'
```

`write_text` returned `6`: the number of characters written (`hello` plus the newline `\n`). `/proc/self/status` describes *the process reading it*, which here is Python itself, so its name is `python3.15`. The last line shows the string-gluing bug that `/` avoids.

Reading `/proc` and `/sys` like `kiln doctor` does ([`snippets/pathlib_1.py`](snippets/pathlib_1.py), Linux only):

```text
(wsl) $ python3.15 docs/python/snippets/pathlib_1.py
first field of /proc/meminfo: MemTotal:
ptrace_scope file exists: True
ptrace_scope value is a digit: True
is a directory: True
has a cgroup.controllers file: True
found some processes: True
```

## Check yourself

1. What is `Path("/var") / "log" / "app.txt"`, and what is its `.suffix`?
   <details><summary>Answer</summary>

   `PosixPath('/var/log/app.txt')`, with suffix `'.txt'`.

   </details>
2. Does `Path("report.json")` create a file?
   <details><summary>Answer</summary>

   No. A Path object is only an address. The file is created when you write to it, for example with `.write_text(...)`.

   </details>
3. Why does Kiln's doctor take `proc_root` as a parameter instead of always reading `Path("/proc")`?
   <details><summary>Answer</summary>

   So tests can pass in a fake folder (under pytest's `tmp_path`) that pretends to be `/proc`. Then every check can be tested on any machine, with any values the test wants.

   </details>
