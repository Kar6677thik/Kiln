# K2: Start methods (fork / spawn / forkserver) + ★ fork-assumption checker

> **Status:** 📝 specified · **Depends on:** K1 · **Branch:** `milestone/k2-start-methods` · **Size:** L
> **Incident:** research §2.9: Python 3.14 changed the Linux default `multiprocessing` start method from `fork` to `forkserver`. Real breakages: krb5, oslo.concurrency, FSL pyfeeds (errata E-05..E-08, [ADR-017](../decisions.md#adr-017)).
> **Reference implementations (read *after* building):**
> - CPython `Lib/multiprocessing/`: `popen_fork.py`, `popen_spawn_posix.py`, `popen_forkserver.py`, `forkserver.py` (especially `main()`, `read_signed`/`write_signed`, and how it reports exit status), `spawn.py` (`get_preparation_data`, `_fixup_main_from_path`), `reduction.py` (`sendfds`/`recvfds`), `util.py` (`_close_stdin`, `Finalize`)
> - vLLM's runtime start-method checks ([design doc](https://docs.vllm.ai/en/latest/design/multiprocessing.html))
> - ★ No static checker for fork assumptions was found (research §00), so there's no reference for the checker itself. Compare its *rules* with the multiprocessing docs' "Programming guidelines" section instead.

---

## 1. The story (plain English)

When a Python program wants more processes, it has to decide **how** to create them. There are three ways.

- **fork:** clone the current process exactly. The copy has all the same variables, open files and loaded code.
- **spawn:** start a brand-new, empty Python and tell it which function to run. It has to import everything again from scratch.
- **forkserver:** at the start, launch one small, clean helper process. Whenever a new process is needed, ask the helper to clone *itself*.

For about 20 years, Linux Python used `fork` by default. So lots of code quietly *depended* on the copy having everything the parent had. A function defined inside another function was fine as the thing to run, because nothing needed to be sent anywhere; the copy already had it. Logging set up in the parent just kept working in the children. A script with no `if __name__ == "__main__":` guard was fine, because nobody re-imported it.

In **Python 3.14** the default on Linux became `forkserver`, because `fork` is unsafe when threads exist. Overnight, real projects broke:

- **krb5** (the MIT Kerberos test suite): a test script with no `__main__` guard got re-imported in the child. That import registered an `atexit` handler that printed a fake failure message, and the daemon start failed with `ConnectionResetError`. They "fixed" it by forcing `fork` again.
- **oslo.concurrency** (OpenStack): tests used *nested functions* as process targets. Those can't be pickled (turned into bytes) to send to a fresh process.
- **FSL pyfeeds**: logging handlers configured in the parent simply didn't exist in the children.

The vLLM team wrote that even *detecting* a missing `__main__` guard "does not appear to be straightforward", so they only check at runtime.

In this milestone we do two things. First, we build Kiln's **own** versions of all three start methods (fork, spawn, forkserver) using raw OS calls. Watching them break teaches what each one copies and what it doesn't. Second, we build something new (★): **`kiln forkcheck`**, a static checker that reads Python source *without running it* and flags code that silently assumes `fork`. Then we confirm each finding by actually running the code under all three methods, and calibrate the checker on faithful reproductions of the real breakages.

## 2. What you will learn

- **Explain** exactly what fork, spawn and forkserver copy, and **predict** which code breaks under each. *(Processes, start methods)*
- **Explain** pickling: what can and can't be pickled and why (`__reduce__`, functions pickled by *qualified name*), and why lambdas and nested functions fail. *(Data model)*
- **Explain** `__main__`, `__mp_main__`, and why `if __name__ == "__main__":` matters under spawn. *(Import system)*
- **Explain** the import system as far as `sys.modules`, module execution and import-time side effects go. *(Import system)*
- **Explain** **fd passing** over Unix sockets (`SCM_RIGHTS`) and fd inheritance (PEP 446, `os.set_inheritable`). *(Processes)*
- **Explain** reparenting: who becomes an orphan's parent, why you can't `waitpid` a non-child, and what a **pidfd** is. *(Processes)*
- **Use** `ast` and `symtable` to analyse code, and **explain** what static analysis can and can't know. *(Static analysis)*
- **Measure** precision and recall for a checker. *(Testing & statistics)*

## 3. Entry criteria

- K1 ✅. The fork launcher, the worker main, the supervisor and the integration tests all exist.
- `kiln doctor --milestone K2` exits 0.

## 4. Scope

### In scope
- `kiln.launch.base` (the `Launcher` protocol), `fork.py` (refactored to the protocol), `spawn.py`, `forkserver.py`, `forkserver_main.py`, and `kiln/worker/__main__.py` (the spawn entry point, `python -m kiln.worker`).
- Config `start.method` and `start.forkserver_preload` ([spec/05](../spec/05-configuration-and-cli.md)), and the forkserver messages of [spec/02 §4.3](../spec/02-protocol.md#43-master--forkserver-k2).
- Orphan handling with pidfds ([spec/04 §6 rule 4](../spec/04-supervisor.md#6-start-methods-k2)).
- ★ `kiln.forkcheck` (scanner, rules FA001–FA008, text and JSON reports, `--confirm`), and `kiln forkcheck` in the CLI.
- `corpus/` (real and synthetic cases), `scripts/fetch-corpus.sh`, `labs/k2/`.
- The lab: start-method cost, plus checker calibration.

### Out of scope (and why)
- Windows/macOS start-method behaviour. Kiln is Linux-only (ADR-003); the primers mention the differences in prose.
- Interprocedural analysis across the *whole* program. FA rules go one call level deep inside a module, plus cross-module import resolution inside the scanned roots (§5.6). Deeper dataflow belongs to the Cartographer project.
- Auto-fixing findings. The checker reports and hints; it doesn't rewrite code.
- Using `multiprocessing` inside Kiln's own launchers ([ADR-012](../decisions.md#adr-012)). It appears only in the confirmation harness and in the corpus.

## 5. Design

### 5.1 The `Launcher` protocol (`kiln/launch/base.py`)

```python
@dataclass(frozen=True, slots=True)
class WorkerLaunchSpec:
    worker_id: int
    generation: int
    config: Config
    channel_child: socket.socket          # the worker's end of the socketpair (master closes its copy after launch)
    close_in_child: tuple[int, ...]        # fork launcher only (spec/04 §6 rule 1)

@dataclass(frozen=True, slots=True)
class Launched:          # the launcher knows the pid immediately (fork, spawn)
    pid: int
@dataclass(frozen=True, slots=True)
class Pending:           # the pid arrives later in a `spawned` message (forkserver)
    pass

LauncherEvent = Spawned | ChildExit | ServerDied     # frozen dataclasses: (worker_id, pid) / (pid, wait_status) / (wait_status)

class Launcher(Protocol):
    name: Literal["fork", "spawn", "forkserver"]
    reaps_children: bool                 # True: the master calls waitpid itself (fork, spawn). False: forkserver reports exits
    def start(self) -> None: ...         # forkserver: spawn the server process and wait for its `ready`; others: no-op
    def launch(self, spec: WorkerLaunchSpec) -> Launched | Pending: ...
    def selectable(self) -> socket.socket | None: ...    # forkserver control socket, registered in the master selector
    def on_readable(self) -> list[LauncherEvent]: ...
    def owns_pid(self, pid: int) -> bool: ...            # used by the reap loop to tell the forkserver itself apart from workers
    def stop(self, timeout_s: float) -> None: ...
```

`Supervisor` (K1) is changed to hold one `Launcher` chosen from `start.method` and to handle `LauncherEvent`s. **No other K1 module changes.** K2 must keep every K1 test green, now parametrised over all three methods where it makes sense (§6 step 2.3).

### 5.2 `fork` (refactor only)

This is the K1 `fork_worker` moved behind the protocol. Behaviour is unchanged. `reaps_children = True`.

### 5.3 `spawn` (`kiln/launch/spawn.py`)

1. `fd = spec.channel_child.fileno()`; `os.set_inheritable(fd, True)`. **WHY:** all fds Python creates are non-inheritable by default (PEP 446), so a fresh program only gets the fds we deliberately mark.
2. `argv = [sys.executable, "-m", "kiln.worker", "--fd", str(fd), "--worker-id", str(id), "--generation", str(gen), "--config-json", config.to_json()]`.
3. `env = dict(os.environ)`, plus `PYTHONPATH` prefixed with `app.pythonpath` entries, plus `KILN_ROLE=worker`.
4. `pid = os.posix_spawn(sys.executable, argv, env)`. The cwd is inherited. No `file_actions` are needed, because the non-inheritable default closes everything else at `exec`. **`posix_spawn`, not `subprocess.Popen`:** `Popen` keeps its own child bookkeeping and may `waitpid` the child itself, which would race with the master's `waitpid(-1)` reap loop. The primer explains this.
5. The parent closes `channel_child`. `reaps_children = True`.
6. `app.preload` is ignored under spawn: the master logs WARNING once, "app.preload has no effect with start.method=spawn: each worker imports the app itself".

`kiln/worker/__main__.py` parses those args, wraps the fd with `socket.socket(fileno=fd)`, and calls `run_worker(... app=None)`. It exits through a **normal** interpreter exit, `sys.exit(code)` ([ADR-028](../decisions.md#adr-028): only forked children use `os._exit`).

### 5.4 `forkserver` (`kiln/launch/forkserver.py` + `forkserver_main.py`)

**Starting it.**
- The master creates `ctrl_master, ctrl_server = socketpair()` and posix_spawns `python -m kiln.launch.forkserver_main --control-fd N --config-json ...` (fd inherited as in §5.3).
- The server imports every module in `start.forkserver_preload`. If `app.preload = true`, the **app module is added to the forkserver's preload list instead of being imported in the master**, so workers share the app's pages via the forkserver and the master stays clean. This is a K2 clarification of spec/05; the spec gets updated in this milestone.
- The server sends `{"type":"ready","pid":…,"preload":[…],"preload_ms":…}` (spec/06 event `forkserver_ready`). The master waits up to `master.boot_timeout_s`; on a timeout, the master exits 4.

**The server loop.** It's single-threaded, with a `selectors` loop over the control socket plus its own SIGCHLD wakeup fd (the same pattern as the master's `SignalPipe`).
- On `spawn_worker`: the fd arrives with the frame (spec/02 §4.3). The server calls `pid = os.fork()`.
  - **Child:** closes the control socket and the wakeup pair, then `run_worker(channel=..., app=<preloaded or None>)` inside `try/finally: finalize_and_exit(code)` ([ADR-028](../decisions.md#adr-028)).
  - **Parent:** closes the received fd, replies `spawned{worker_id, pid}`, logs `forkserver_forked`.
- On SIGCHLD: run the `waitpid(-1, WNOHANG)` loop and send `child_exit{pid, wait_status}` for each child.
- On control-socket **EOF** (the master died): send SIGTERM to every child, wait up to 5 s, SIGKILL the rest, then exit 0. **WHY:** without this, a crashed master would leave orphan workers running forever.
- On `shutdown`: stop accepting `spawn_worker`, and exit once the last child has been reaped.

**fd framing detail.** The master sends the frame header + body + fd with a single `socket.send_fds(sock, [frame_bytes], [fd])`. The server reads with `socket.recv_fds(sock, 65536, 1)` and feeds the bytes to its `FrameDecoder`. Any fds received are queued in a FIFO, and each decoded `spawn_worker` frame pops exactly one fd. If a `spawn_worker` frame arrives with no fd queued, that's a protocol error: log CRITICAL and exit 1 (the master treats it as `forkserver_died`). The primer `fd-passing.md` must draw this.

**Master side.**
- `on_readable()` turns `spawned` into `Spawned` and `child_exit` into `ChildExit`. The supervisor feeds `ChildExit` into the *same* `handle_exit(pid, status)` used for direct children.
- When `Spawned` arrives, the master opens `os.pidfd_open(pid)` **immediately** and keeps it on the WorkerHandle, unused unless the worker becomes an orphan (spec/04 §6 rule 4).
- The forkserver itself is a direct child of the master, so the master reaps it. Its death produces `ServerDied`: the master logs `forkserver_died` (CRITICAL), restarts it with the spec/04 §5.1 backoff, and marks every forkserver-owned worker **orphaned**. For orphans, the pidfd is registered in the master's selector. Readable means it exited, which is classified `unknown_exit`, and its jobs become `worker_lost`.
- `reaps_children = False` for workers.

### 5.5 Fork-safety facts every launcher test checks

| Check | fork | spawn | forkserver |
|---|---|---|---|
| fd hygiene (AC-1.14 style, inode comparison) | ✅ | ✅ | ✅ (also: no fd of the *forkserver's* control socket in the worker) |
| `random` differs across workers (`labs.random_probe`, a new lab task returning `random.getrandbits(64)`) | ✅ | ✅ | ✅ |
| Signal dispositions per spec/04 §3.1 (read `/proc/<pid>/status` `SigIgn`/`SigCgt` bitmasks) | ✅ | ✅ | ✅ |
| Workers are in the master's process group (spec/09 §4.5) | ✅ | ✅ | ✅ (the test that proves the sweep works) |
| Parent's atexit handlers not run in the child (G1.7 style) | ✅ | n/a (fresh interpreter) | ✅ |

### 5.6 ★ `kiln.forkcheck`: the scanner

**Pipeline** (`scanner.py`):
1. Collect `.py` files under the given PATHs. `--exclude GLOB` is repeatable. Default excludes: `.venv`, `__pycache__`, `build`.
2. For each file, `ast.parse(source, filename, type_comments=False)`. A `SyntaxError` produces finding **FA000** (`info`, "could not parse"), and the scan continues.
3. Build a `ModuleInfo` (`symbols.py`):
   - the import alias table: `import multiprocessing as mp`, `from multiprocessing import Process as P`, `from concurrent.futures import ProcessPoolExecutor`;
   - the module-level definitions (functions and classes, with line numbers);
   - for every function, its `symtable` entry: nested or not, free variables, `global` declarations;
   - **guard regions:** the line ranges of the bodies of `if __name__ == "__main__":`, with the comparison accepted in either order, `==` only;
   - **top-level statements:** module body statements outside guard regions, *recursing into* top-level `if`/`try`/`with`/`for`/`while`, but not into `def`/`class` bodies.
4. **Process-creation sites** (`ProcSite`): a `Call` whose callee resolves (through aliases) to one of these APIs. Each site records the call node, the enclosing function (or `<module>`), the start-method context if known (an explicit `get_context("fork")` receiver means `fork`), and the target or callable expression.

   | API | Target or callable argument |
   |---|---|
   | `multiprocessing.Process`, `multiprocessing.context.*Process`, `<ctx>.Process` | `target=` kwarg, or the 2nd positional |
   | `multiprocessing.Pool`, `<ctx>.Pool` | the `.map`/`.imap`/`.imap_unordered`/`.starmap`/`.apply`/`.apply_async`/`.map_async` call's first argument, tracked via the variable or `with ... as pool` |
   | `concurrent.futures.ProcessPoolExecutor` | `.submit`/`.map` first argument |
   | `multiprocessing.Manager`, `<ctx>.Manager` | none (it's a process start, though) |
   | a **subclass of `Process`** defined in a scanned module, then `.start()` called on an instance of it | the class's `run` method |
   | `--process-api dotted.name` (a user-declared wrapper, repeatable) | none |
5. **Cross-module resolution:** an import of a module that lives inside the scanned roots (resolved relative to the importing file's directory, then each root) links the two `ModuleInfo`s. That's what lets FA005 see that `t_otp.py` imports `k5test`, which registers an `atexit` handler.
6. Run every rule from `rules.py`. Each rule is a function `def check(mod: ModuleInfo, project: Project) -> Iterator[Finding]`, registered in `RULES` by id.

```python
@dataclass(frozen=True, slots=True)
class Finding:
    rule: str            # "FA002"
    name: str            # "unpicklable-target"
    severity: Literal["error", "warning", "info"]
    path: str; line: int; col: int; end_line: int
    message: str         # what and why, in plain English
    hint: str            # how to fix
    breaks_under: tuple[str, ...]   # subset of ("spawn", "forkserver", "fork")
    motivation: str      # e.g. "oslo.concurrency (errata E-06)"
```

### 5.7 ★ The rule catalogue

Severity meanings: **error** means it will fail under spawn/forkserver. **warning** means it will probably behave differently. **info** means it works, but relies on `fork` explicitly.

| ID | Name | Severity | Motivated by |
|---|---|---|---|
| FA001 | `unguarded-process-start` | error | krb5 `t_otp.py` (E-05) |
| FA002 | `unpicklable-target` | error | oslo.concurrency (E-06) |
| FA003 | `parent-only-logging` | warning | FSL pyfeeds (E-07) |
| FA004 | `runtime-global-not-inherited` | warning | "state not inherited" (oslo/research §00) |
| FA005 | `import-time-side-effect` | warning | krb5 `k5test` atexit (E-05) |
| FA006 | `parent-patch-not-inherited` | warning | git-repo (E-08) |
| FA007 | `unpicklable-argument` | error | general |
| FA008 | `explicit-fork-reliance` | info | krb5 fix, pexpect (E-05, E-08) |

#### FA001 `unguarded-process-start`
- **Detects:** a ProcSite (or a call to a same-module function that contains a ProcSite, one level deep) that executes as a **top-level statement**, meaning not inside a `def`/`class` body and not inside a `__main__` guard region.
- **Why it breaks:** spawn and forkserver children re-import the main script as `__mp_main__`. Unguarded top-level code runs again in the child. For multiprocessing that raises "An attempt has been made to start a new process before the current process has finished its bootstrapping phase", or it re-runs side effects.
- **Positive:**
  ```python
  import multiprocessing as mp
  def work(): print("hi")
  p = mp.Process(target=work); p.start(); p.join()
  ```
- **Negative:** the same three lines under `if __name__ == "__main__":`, or inside `def main():` that is only called under the guard.
- **Known false positives:** files that are never the main script and never imported by a child. They're still flagged, because a top-level process start in an importable module is a hazard anyway. Scripts that always force `fork`: if the same module calls `set_start_method("fork")` at top level *before* the site, FA001 is downgraded to `info` and FA008 is reported.
- **Hint:** "Move process creation under `if __name__ == '__main__':` (see docs/concepts/main-module.md)."

#### FA002 `unpicklable-target`
- **Detects:** the target or callable of a ProcSite is
  - a `Lambda`;
  - a name that resolves (via `symtable`: the function is defined inside another function's scope) to a **nested function**;
  - a method of a class defined inside a function;
  - `functools.partial(<one of the above>, ...)`;
  - or a **bound method of an instance whose class is defined at module level but whose instance holds a known-unpicklable attribute** (only when it's visible in the same function, via FA007's tracking).
- **Why it breaks:** spawn and forkserver must *pickle* the target. Functions pickle **by qualified name** (`module.qualname`), and a nested function's qualname (`outer.<locals>.inner`) can't be looked up. Under fork nothing is pickled for `Process` (the child inherits memory), which is why the bug hid for years.
- **Pool nuance:** `Pool.map(fn, ...)` pickles `fn` **even under fork**. Pool sites therefore get `breaks_under = ("fork", "spawn", "forkserver")` and a message saying it fails everywhere.
- **Positive:** `def test(): def target(): ...; mp.Process(target=target).start()`.
- **Negative:** a module-level `def target():` or `@staticmethod` on a module-level class.
- **Known false positives:** a site with an explicit `get_context("fork")` receiver: still reported, but at `info`, because it works but opts in to fork.

#### FA003 `parent-only-logging`
- **Detects:** a module where all three hold:
  - (a) logging is configured (`logging.basicConfig`, `logging.config.dictConfig`/`fileConfig`, `<logger>.addHandler`, or `logging.getLogger().setLevel`) **only** inside a guard region or inside functions reachable from it;
  - (b) a ProcSite's target, resolved in this module, calls a logging method (`logging.info/...`, `<name>.info/debug/warning/error/exception/critical/log` where the name was bound from `logging.getLogger(...)`);
  - (c) neither the target nor a Pool `initializer=` function configures logging.
- **Why it breaks:** handlers are objects in the parent's memory. Spawn and forkserver children start with no handlers, so records go to the "last resort" handler (WARNING and above to stderr, unformatted) or vanish.
- **Negative:** the target calls a `setup_logging()` that configures logging, a Pool with `initializer=setup_logging`, or a `QueueHandler`/`QueueListener` pattern (detected by a `logging.handlers.QueueHandler` reference in the target or initializer).
- **Known false positives:** logging configured by a framework at import time, which the checker can't see. The hint tells the user to suppress it with `# forkcheck: ignore[FA003]`.

#### FA004 `runtime-global-not-inherited`
- **Detects:** a ProcSite target (resolved in this module) **reads** a module-level name `G`, and `G` is **rebound** inside some function via `global G` (symtable `is_declared_global`), **or** mutated by subscript or attribute assignment (`G[k] = v`, `G.attr = v`) inside a function. That function is called in the parent before the site (either in the same function body before the site's line, or anywhere in the guard region before it).
- **Why it breaks:** spawn and forkserver children re-import the module and see `G`'s *import-time* value, not the value the parent assigned later.
- **Positive:**
  ```python
  CONFIG = {}
  def load(): CONFIG["db"] = "prod"
  def work(): print(CONFIG["db"])
  if __name__ == "__main__": load(); mp.Process(target=work).start()
  ```
- **Negative:** `CONFIG` passed as an argument to the target, or assigned at module level only.
- **Known false positives:** a mutation that sets the same value as import time. That isn't detectable statically, so it's accepted as a warning.

#### FA005 `import-time-side-effect`
- **Detects:** in a module that is (a) the main script of a ProcSite, or (b) imported (transitively, within the scanned roots) by a module containing a ProcSite, a **top-level statement** (outside guards) that calls `atexit.register`, `signal.signal`, `threading.Thread(...).start()` / `<Thread>.start()`, `os.fork`, `sys.excepthook = ...`, `logging.basicConfig`, `socket.socket(...).bind`, or `open(..., "w"/"a")`.
- **Why it matters:** under spawn and forkserver, the child re-imports these modules and **repeats** the side effect. krb5's `k5test` registered an `atexit` handler on import, so every child printed a bogus "re-run this test" message.
- **Reported on:** the side-effect line, with the message naming the chain ("imported by `t_otp.py`, which starts processes at line 40").
- **Negative:** the same calls inside functions, or under a guard.
- **Known false positives:** idempotent side effects (registering the same harmless handler). They're reported anyway, as warnings.

#### FA006 `parent-patch-not-inherited`
- **Detects:** inside a function containing a ProcSite, or inside a test function or method decorated with `mock.patch*`, an active patch that is still in effect at the ProcSite line:
  - a `unittest.mock.patch(...)` / `patch.object(...)` / `patch.dict(...)` used as a decorator, as a `with` context, or via `.start()`;
  - `monkeypatch.setattr(...)`;
  - a direct assignment to an attribute of an imported module or class (`SomeClass.attr = ...`, `mod.func = ...`).
- **Why it breaks:** a patch modifies objects in *this* process. Spawn and forkserver children import fresh, unpatched modules. git-repo hit exactly this with a class-level mock.
- **Negative:** a patch applied in the target itself, or a value passed as an argument.

#### FA007 `unpicklable-argument`
- **Detects:** an argument passed to a ProcSite (`args=`/`kwargs=` of Process, or Pool/Executor call arguments) that is a name bound earlier *in the same function* to `threading.Lock()`/`RLock()`/`Condition()`/`Semaphore()`, `open(...)`, `socket.socket(...)`, `sqlite3.connect(...)`, a generator expression, a call to a generator function defined in the module, or a `Lambda`; or a `with open(...) as f` target; or such an expression inline.
- **Why it breaks:** these objects can't be pickled (`TypeError: cannot pickle '_thread.lock' object`). `multiprocessing.Lock()` is fine for `Process` args, but not through Pool arguments; the hint explains this.
- **`breaks_under`:** Process args are spawn and forkserver only (fork doesn't pickle Process args). Pool and Executor args are all three methods.

#### FA008 `explicit-fork-reliance`
- **Detects:** `set_start_method("fork")`, `get_context("fork")`, `os.fork()`.
- **Why report it:** it isn't a bug today. But fork is unsafe with threads (the 3.12+ `DeprecationWarning`) and isn't the default anywhere anymore, so this code *depends on* fork. It's also how krb5 and pexpect "fixed" their breakage.
- **Counted separately** in calibration (it isn't a "breakage" finding).

**Suppression:** `# forkcheck: ignore[FA00x]` on the finding's line, or `# forkcheck: ignore` for all rules.

### 5.8 Output formats and exit codes

- **Text** (the default). Findings are sorted by path and then line:
  ```
  corpus/real/oslo-lockutils/distilled.py:31:17: FA002 [error] unpicklable-target: nested function 'target' is passed as a Process target; spawn/forkserver must pickle it by name and 'test_lock.<locals>.target' cannot be found (breaks under: spawn, forkserver) — hint: move 'target' to module level
  ```
  It ends with a summary: counts per severity, the number of files scanned, and the number of parse errors.
- **JSON** (`--format json`):
  ```json
  {"tool":"kiln-forkcheck","version":1,"files_scanned":3,
   "findings":[{"rule":"FA002","name":"unpicklable-target","severity":"error","path":"...","line":31,"col":17,"end_line":31,
                "message":"...","hint":"...","breaks_under":["spawn","forkserver"],"motivation":"oslo.concurrency (errata E-06)",
                "confirmation":null}]}
  ```
- **Exit codes:** `0` = no finding at or above `--fail-on` (default `warning`); `1` = findings; `2` = usage error (bad args, PATH missing). Parse errors alone don't cause exit 1.

### 5.9 `--confirm` (dynamic confirmation; `forkcheck/confirm.py`)

`kiln forkcheck PATH... --confirm ENTRY.py [--confirm-args "..."] [--confirm-timeout 60]` runs ENTRY once per start method in a fresh subprocess, using this **bootstrap** (with `M` and `ENTRY` filled in):

```python
import multiprocessing as mp, runpy, sys
mp.set_start_method(M, force=True)
sys.argv = [ENTRY, *ARGS]
runpy.run_path(ENTRY, run_name="__main__")
```

The subprocess runs with `cwd = dirname(ENTRY)` and the scanned roots on `PYTHONPATH`.

- **Outcome per method:** `ok` (exit 0), `exception:<Type>` (the last traceback line's exception type, parsed from stderr), `signal:<NAME>`, `exit:<code>`, or `timeout` (a hang, killed after the timeout, via the process group).
- **Normalised output:** stdout and stderr with pids, hex addresses and timings replaced (the spec/09 §7 normaliser).
- **Confirmed:** a finding in a file reachable from ENTRY (by import) is `confirmed = true` if the outcome or the normalised output under spawn **or** forkserver differs from the fork baseline, and `false` if all three are identical. JSON gets `"confirmation": {"fork": "...", "forkserver": "...", "spawn": "...", "differs": true}`.
- **Limitation (stated in the help text and the primer):** confirmation is per entry point, not per finding. It shows *that* behaviour changes, not *which* finding caused it.
- **Fallback** (gate G2.5): if `runpy.run_path` with `run_name="__main__"` doesn't make spawn children find the main module, use a temporary copy `ENTRY_DIR/_kiln_confirm_<name>.py`. It's the original source with the 2-line `set_start_method` preamble inserted after any `from __future__` imports and the module docstring. It's run directly and deleted afterwards (try/finally).

### 5.10 The corpus (layout from [spec/08](../spec/08-repo-layout-and-tooling.md), rules from ADR-017)

```
corpus/real/<case>/
    distilled.py        # the entry point; ≤ 120 lines, written fresh (not copied), preserving the failing pattern
    <helpers>.py        # e.g. k5test_like.py for krb5 (side effect on import)
    PROVENANCE.md       # upstream links, short quoted diff, what was simplified and why, date retrieved
    expected.json
corpus/synthetic/FA00x/
    pos_<what>.py       # each has "# expect: FA00x" on the line(s) that must be flagged
    neg_<what>.py       # must produce no FA00x finding
```

| Case | Required for SC-5? | Pattern |
|---|---|---|
| `krb5-t_otp` | **yes** | a script with no `__main__` guard starting a `Process` subclass (a RADIUS-like daemon) at top level, plus an imported helper registering an `atexit` handler that prints a failure banner unless `success()` ran |
| `oslo-lockutils` | **yes** | test functions defining nested `target` and `locked` functions passed to `Process` |
| `fsl-pyfeeds` | **yes** | logging configured in `main()` under the guard; children log via `logging.getLogger(__name__)` (reconstructed if the source stays unreachable: errata E-07) |
| `git-repo-mock` | stretch (E-08) | a class-level `mock.patch.object` in the parent; the child reads the attribute |
| `pexpect-context` | stretch (E-08) | the context-forcing pattern. The expected findings are filled in from evidence in K2 step 2.6 |

`expected.json`:

```json
{"entry": "distilled.py",
 "must_fire":     [{"rule":"FA001","path":"distilled.py","line":40}, {"rule":"FA005","path":"k5test_like.py","line":12}],
 "may_fire":      [{"rule":"FA008"}],
 "must_not_fire": [{"rule":"FA002"}],
 "behaviour":     {"fork":"ok", "forkserver":"differs", "spawn":"differs"}}
```

**Matching:** a finding matches an expectation when the rule and path are equal and the line is equal (the line is checked exactly; distilled files are under our control). Synthetic files use the `# expect:` comment lines as `must_fire`. Every other finding in a `neg_` file is a false positive.

**Synthetic minimum:** ≥ 4 `pos_` and ≥ 4 `neg_` files per rule FA001–FA007, covering at least each "Positive", "Negative" and "Known false positive" bullet in §5.7.

**Upstream (stretch):** `scripts/fetch-corpus.sh` clones into `corpus/upstream/` (git-ignored): krb5 at the parent commit of PR #1483's merge, and oslo.concurrency at the 7.2.0 tag. The pinned SHAs go in the script, with comments. Running the checker on those directories goes into the lab report as "upstream calibration (informational)".

### 5.11 Calibration metrics (`labs/k2/confirm_harness.py`, lab id `k2-calibrate`)

- For each rule: TP, FP, FN, then **precision** = TP/(TP+FP) and **recall** = TP/(TP+FN), over synthetic plus real `must_fire`/`must_not_fire`.
- The record has one metric per rule (`precision_FA00x`, `recall_FA00x`, unit `ratio`), plus `real_cases_detected` (a count).
- The confirmation outcome table per real case comes from `--confirm`.
- **Targets** (the acceptance bar, not predictions): real required cases recall = 1.0 (SC-5); synthetic precision ≥ 0.9 and recall ≥ 0.9 per rule. Misses are listed and explained in the lab report.

## 6. Steps

| # | Goal | Docs first | Tests | Code | Done when |
|---|---|---|---|---|---|
| 2.1 | **Concepts + launcher protocol** | concepts `start-methods`, `pickling`, `main-module`, `import-system`; python `if-name-main`, `pickle-and-reduce` | K1 tests still green after the refactor | `launch/base.py`; `fork.py` behind the protocol; supervisor uses a `Launcher` | no behaviour change |
| 2.2 | **spawn** | concept `fd-passing` (part 1: inheritance, PEP 446); python `runpy` (for `-m`) | `tests/integration/test_spawn_launcher.py`: ready, run a job, fd hygiene, random differs, spawn re-imports the app (a module counter written to a temp file is incremented per worker) | `launch/spawn.py`, `worker/__main__.py` | AC-2.1 |
| 2.3 | **forkserver** | concepts `fd-passing` (part 2: SCM_RIGHTS), `reparenting-and-orphans` | `test_forkserver_launcher.py`: ready, run a job, preload shared (the app module imported once in the forkserver, verified via a temp-file counter), `child_exit` classification for every `labs.crash` mode, the forkserver SIGKILL → restart → orphans detected via pidfd, master death → the forkserver kills children (no stray processes, spec/09 §4.5); **all K1 integration tests parametrised over the 3 methods** (except fork-specific ones) | `launch/forkserver.py`, `forkserver_main.py`; supervisor `LauncherEvent` handling | AC-2.2, AC-2.3 |
| 2.4 | **Corpus first (real cases)** | concept `static-vs-dynamic-analysis` | the distilled cases run under the confirm bootstrap with the expected `behaviour` | retrieve sources (gates G2.6–G2.8); write `corpus/real/*` + PROVENANCE + expected.json | the 3 required cases reproduce their breakage under forkserver/spawn |
| 2.5 | **Scanner + symbols** | concept `ast-and-symtable`; python `ast-module`, `symtable-module`, `closures`, `functools-partial` | `tests/unit/forkcheck/test_symbols.py`: aliases, guard regions (both orders, nested if), top-level recursion, ProcSite detection for every API row, cross-module resolution | `forkcheck/scanner.py`, `symbols.py` | ProcSites found in all corpus files |
| 2.6 | **Rules FA001–FA008 + synthetic corpus** | each rule's docstring = its §5.7 entry | `tests/unit/forkcheck/test_rules.py`: one test per synthetic file (parametrised) | `forkcheck/rules.py`, `corpus/synthetic/**`; finalise the pexpect/git-repo expectations | AC-2.5 |
| 2.7 | **Reports + CLI + suppression** | — | `test_report.py`: text and JSON goldens, exit codes, `--fail-on`, `ignore` comments | `forkcheck/report.py`, `__init__.py`, `cli.py forkcheck` | AC-2.6 |
| 2.8 | **--confirm** | — | `tests/integration/test_confirm.py` on the 3 required cases | `forkcheck/confirm.py` (+ fallback if G2.5 fails) | AC-2.4 |
| 2.9 | **Lab + calibration** | — | `tests/lab/test_k2_lab_smoke.py` | `labs/k2/confirm_harness.py`, `labs/k2/startup_cost.py`; `scripts/fetch-corpus.sh` | lab report complete |

## 7. Lab design

Lab report: `docs/labs/K2-forkcheck.md`. Lab ids: `k2-startup`, `k2-calibrate`.

### (a) What does each start method cost?
- **Question:** how long does it take from "launch requested" to `ready`, and how much memory does each worker really use, for fork vs forkserver vs spawn?
- **Hypothesis:**
  - **Time:** fork < forkserver (after its first start) ≪ spawn, because spawn re-runs interpreter startup and all imports.
  - **Memory:** PSS per worker is lowest for fork with preload; forkserver is close; spawn is highest, because nothing is shared.
  - The forkserver's *first* start costs about as much as one spawn.
- **Independent variables:** `start.method` ∈ {fork, forkserver, spawn} × app weight ∈ {`examples.demo` (light), `examples.demo` + `labs.preload_mb=100` (heavy; uses K3's preload, or a stand-in `labs/k2/heavy_app.py` that imports a 100 MiB module-level structure if K3 isn't done)} × filesystem ∈ {ext4 snapshot (primary), 9P `/mnt/c` (secondary, to show the ADR-003 effect on spawn)}.
- **Dependent variables:**
  - `launch_to_ready_ms`: the master logs both timestamps (`time.monotonic()`), per worker;
  - `pss_bytes`, `uss_bytes` and `rss_bytes` per worker after ready, from `smaps_rollup` (K3's reader; in K2 a minimal reader in `labs/k2/` is fine if K3 isn't done);
  - the total PSS of the master, forkserver and workers.
- **Controls:** 4 workers, 3.15, an idle machine, the spec/10 §5 noise controls.
- **Repeats:** per [spec/10 §3](../spec/10-measurement-methodology.md#3-repeats-and-warm-ups).
- **Result table shape:** `method | app | fs | launch→ready ms (median, 95% CI) | PSS/worker | USS/worker | total PSS`.

### (b) Checker calibration
- **Question:** does `forkcheck` find the real breakages, and how precise is it?
- **Hypothesis:** all three required real cases are detected (FA001+FA005 for krb5, FA002 for oslo, FA003 for pyfeeds), each confirmed by `--confirm`. Synthetic precision and recall per rule meet the §5.11 bar. FA003/FA004 have the most false positives, since they're the most heuristic rules.
- **Procedure:** `./scripts/dev.sh lab k2-calibrate`.
- **Result table shapes:** `rule | TP | FP | FN | precision | recall`; `case | must_fire found? | confirmed? | fork | forkserver | spawn`.

## 8. Verification gates (ADR-020)

| # | Claim to verify | How to check (command) | If false, fallback |
|---|---|---|---|
| G2.1 | On 3.14+ Linux, `multiprocessing.get_start_method()` defaults to `forkserver`; on 3.13 to `fork` | `for v in 3.13 3.14 3.15; do uv run -p $v python -c "import multiprocessing as m; print(m.get_start_method())"; done` | correct the primer and errata (research §2.9 claim) |
| G2.2 | `os.posix_spawn` inherits only fds marked inheritable | `test_spawn_launcher.py` fd hygiene | add `file_actions` closing the others, or switch to `os.fork()` + `os.execv` (documented) |
| G2.3 | `socket.send_fds`/`recv_fds` exist and work on AF_UNIX stream sockets | snippet `concepts/snippets/fd_passing.py` | use `sock.sendmsg` with `SCM_RIGHTS` ancillary data by hand (also a good teaching moment) |
| G2.4 | `os.pidfd_open` is available (Linux ≥ 5.3, Python ≥ 3.9), and a pidfd becomes readable when a non-child exits | snippet `concepts/snippets/pidfd_orphan.py` | poll `/proc/<pid>/stat` starttime (pid-reuse-safe comparison) every second; document it |
| G2.5 | `runpy.run_path(ENTRY, run_name="__main__")` lets spawn/forkserver children locate and re-import the main module | `test_confirm.py` on a tiny script that prints `__name__` in the child | the temp-copy fallback of §5.9 |
| G2.6 | krb5 PR #1483 and `t_otp.py` (pre-fix) are retrievable | `git clone https://github.com/krb5/krb5` + `git log -- src/tests/t_otp.py` | distill from the errata E-05 description; PROVENANCE says "source not inspected" |
| G2.7 | the oslo.concurrency 7.2.0 `test_lockutils.py` and the Debian/Gentoo patches are retrievable | `sources.debian.org` patch page; PyPI sdist | as above (E-06) |
| G2.8 | FSL pyfeeds MR 46 is reachable | `curl -sI https://git.fmrib.ox.ac.uk/fsl/pyfeeds/-/merge_requests/46` | reconstruct from the pattern (E-07) |
| G2.9 | `symtable` APIs used (`Function.get_frees`, `Symbol.is_declared_global`, `SymbolTable.get_children`) behave the same on 3.13–3.15, and no deprecated ones are used | `test_symbols.py` on the matrix | an adapter function per version |
| G2.10 | Removed AST classes (`ast.Num`, `ast.Str`) aren't needed; only `ast.Constant` is used | ruff/mypy + tests on 3.15 | — |
| G2.11 | forkserver workers stay in the master's process group | `os.getpgid(worker) == os.getpgid(master)` in a test | the forkserver calls `os.setpgid(0, master_pgid)` in each child; document it |

## 9. Acceptance criteria

- **AC-2.1** With `start.method = spawn`, every applicable K1 integration test passes. Workers pass the fd-hygiene, random and signal-disposition checks of §5.5.
- **AC-2.2** The same for `forkserver`. The app module is imported **once**, in the forkserver, not in the master or the workers (temp-file counter).
- **AC-2.3** Killing the forkserver with SIGKILL: the master logs `forkserver_died`, restarts it, and keeps serving. Orphaned workers that later exit are detected through their pidfds and classified `unknown_exit`, with their jobs `worker_lost`. Killing the master: no worker survives longer than 6 s (forkserver EOF handling).
- **AC-2.4** `kiln forkcheck corpus/real/<case> --confirm corpus/real/<case>/distilled.py` reports at least one **confirmed** must-fire finding for each of `krb5-t_otp`, `oslo-lockutils` and `fsl-pyfeeds` (**SC-5**).
- **AC-2.5** Synthetic calibration: precision ≥ 0.9 and recall ≥ 0.9 for every rule FA001–FA007. Any rule below the bar is listed in the lab report, with the reason and an issue in "Stretch / later". The learner has to accept this explicitly at sign-off.
- **AC-2.6** Text and JSON output match their goldens. Exit codes follow §5.8. `# forkcheck: ignore[...]` works.
- **AC-2.7** The lab report (a) and (b) tables are filled from published records.
- **AC-2.8** All docs deliverables exist. `dev.sh check` and `test-matrix` are green.

## 10. Docs deliverables

| Kind | Path | Must cover |
|---|---|---|
| Concept | `docs/concepts/start-methods.md` | the three methods side by side (what's copied, cost, breakage), with diagrams; the 3.14 default change; why fork + threads is unsafe (link `fork-and-threads`); where each is the default on Linux/macOS/Windows |
| Concept | `docs/concepts/pickling.md` | what pickling is; by-reference pickling of functions and classes (qualname lookup); `__reduce__`; `copyreg`; why lambdas, nested functions, locks and files fail; **why unpickling untrusted data is dangerous** (ADR-009) |
| Concept | `docs/concepts/main-module.md` | `__name__`, `"__main__"`, `__mp_main__`; what spawn does to the main script; why the guard fixes it; krb5's story |
| Concept | `docs/concepts/import-system.md` | `sys.modules`, finders/loaders (overview), module code runs once per process, import-time side effects, why a fresh process re-runs them |
| Concept | `docs/concepts/fd-passing.md` | inheritance across exec (PEP 446, `set_inheritable`); SCM_RIGHTS over Unix sockets; why the fd *number* differs on the other side; our framing + fd FIFO design |
| Concept | `docs/concepts/reparenting-and-orphans.md` | what happens to children when a parent dies (reparenting to init or a subreaper); why only the parent can `waitpid`; pid reuse; pidfds; the forkserver EOF rule |
| Concept | `docs/concepts/ast-and-symtable.md` | source → tokens → AST; walking with `ast.NodeVisitor`; scopes, free variables and `global` via `symtable`; what static analysis can't know |
| Concept | `docs/concepts/static-vs-dynamic-analysis.md` | soundness vs completeness; false positives and negatives; precision and recall; why we confirm dynamically |
| Python | `docs/python/if-name-main.md` | the guard idiom, with demo output under spawn |
| Python | `docs/python/pickle-and-reduce.md` | `pickle.dumps`/`loads`, protocols, `__reduce__`, what "picklable" means |
| Python | `docs/python/runpy.md` | `python -m`, `run_path`, `run_module` |
| Python | `docs/python/closures.md` | nested functions, captured variables, `__qualname__`, `<locals>` |
| Python | `docs/python/functools-partial.md` | `partial` objects, and why partial(nested) still can't be pickled |
| Python | `docs/python/ast-module.md` | `ast.parse`, node types, `NodeVisitor`, `lineno`/`col_offset`, `ast.dump` |
| Python | `docs/python/symtable-module.md` | `symtable.symtable`, scopes, symbol flags |
| Python | `docs/python/multiprocessing-basics.md` | `Process`, `Pool`, `get_context`, `set_start_method`. Just enough to read the corpus |
| Tour | `docs/tours/K2.md` | stops: Launcher protocol → spawn → worker `__main__` → forkserver_main loop → fd FIFO → pidfd orphans → scanner → one rule end-to-end (FA002) → confirm |
| Lab | `docs/labs/K2-forkcheck.md` | experiments (a) and (b) |
| Corpus | `corpus/real/*/PROVENANCE.md` | per §5.10 |
| Spec update | `docs/spec/05-configuration-and-cli.md` | `app.preload` meaning under forkserver/spawn (§5.3, §5.4) |
| Spec update | `docs/spec/03-jobs-and-tasks.md` §7 | add `labs.random_probe` |

## 11. Reference comparison assignment

The lab report's "Reference comparison" must answer:
1. `multiprocessing/forkserver.py`: how does CPython's forkserver report a child's exit status to the parent (look at `write_signed` and the `os.waitstatus_to_exitcode` use)? Compare with our `child_exit` message.
2. How does CPython authenticate connections to its forkserver (the `authkey`, the listener address)? Why don't *we* need that? (Hint: socketpair vs a filesystem socket.)
3. `popen_spawn_posix.py` + `spawn.py`: what "preparation data" does CPython send to a spawned child (`sys.path`, the main module path, `sys.argv`...)? What did we send instead, and why don't we need most of it?
4. What does `multiprocessing` do about orphans? Does its forkserver kill children when the parent dies?
5. vLLM chose runtime checks over static detection. After building forkcheck, where do you agree with them, and where does static analysis add value?
6. Where does CPython use `os.register_at_fork`, and for what?

## 12. Risks and pitfalls

| Risk | Mitigation |
|---|---|
| Losing the fd/frame pairing on the forkserver socket | the FIFO design + a test sending 50 `spawn_worker` requests back-to-back |
| `posix_spawn` env/argv quoting with the space in the repo path | pass argv as a list (no shell); a test runs from the real repo path |
| Forkserver-mode test flakiness from extra process hops | condition-based waits (spec/09 §4.1); longer boot timeouts in tests |
| The checker growing into a general linter | the rule list is fixed (FA000–FA008); new rule ideas go to Stretch |
| A heuristic rule (FA003/FA004) too noisy | the synthetic negatives pin the expected *silence*; the lab reports the measured precision |
| Copying upstream code into the corpus | distilled files are written fresh; PROVENANCE quotes ≤ 15 lines per source with attribution (ADR-017) |
| Confirm-harness hangs (children waiting forever) | the process-group kill after the timeout; `timeout` is an outcome, not a crash |

## 13. Checkpoint quiz (seed questions)

1. Under spawn, why does `mp.Process(target=lambda: 1)` fail, but it works under fork?
2. What does a spawned child do with the main script, and why does that make the `__main__` guard necessary?
3. Our spawn launcher uses `os.posix_spawn` instead of `subprocess.Popen`. What would go wrong with Popen in our design?
4. Why can't the master `waitpid` a forkserver-created worker? How does it learn the worker's exit status?
5. If the forkserver dies, how does the master find out when an orphaned worker exits, and why not `os.kill(pid, 0)`?
6. Name two things a forked child inherits that a spawned child doesn't.
7. Why is `Pool.map(nested_fn, xs)` broken even under fork, while `Process(target=nested_fn)` isn't?
8. What does `symtable` tell us that `ast` alone doesn't?
9. A rule has precision 1.0 and recall 0.5. What does that mean in words?
10. What does `--confirm` prove, and what *doesn't* it prove?

## 14. Stretch / later

- Run on the upstream checkouts (`fetch-corpus.sh`); report as informational.
- Promote `git-repo-mock` and `pexpect-context` to required cases.
- SARIF output for editor integration.
- An FA009 idea: `threading.Thread` started in the parent before a fork (it overlaps with `fork-and-threads`).
