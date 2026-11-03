# Code Style (binding for every implementation round)

> **Companion to:** [`documentation.md`](documentation.md), which covers docstrings and comment tags, and [`../spec/08-repo-layout-and-tooling.md`](../spec/08-repo-layout-and-tooling.md), which covers ruff, mypy and naming tables.
> **The one-line version:** write the most *plain, explicit, boring* Python that does the job. The reader is new to Python ([ADR-002](../decisions.md#adr-002)). Every clever line is a line they can't read yet.

---

## 1. Plain Python preferred

| ❌ Clever | ✅ Plain | Why |
|---|---|---|
| `ready = [w for w in ws if w.state is S.READY and w.free()] or None` | an explicit `for` loop that appends, then `if not ready: return None` | one idea per line; you can put a breakpoint in it |
| `if (n := len(buf)) >= 4:` | `n = len(buf)` then `if n >= 4:` | the walrus operator is a new concept for no gain here |
| `x = a if b else c if d else e` | `if/elif/else` | nested conditional expressions are hard to read |
| `functools.reduce(lambda a, b: a | b, flags)` | a `for` loop | — |
| `getattr(self, f"_handle_{msg['type']}")(msg)` | an explicit `dict` of `{"ready": self._on_ready, ...}` | dynamic attribute lookup hides which handlers exist; the dict is a readable table |
| metaclasses, `__init_subclass__` registries, descriptors in core code | plain classes and functions | these are *subjects* of study in Cartographer, not tools we need here |

When a less-plain construct is genuinely the right tool (a dataclass, a context manager, an `Enum`, a `Protocol`), use it, with a `# PY:` comment at its first use in the file and a Python note ([documentation.md §3](documentation.md#3-python-notes-docspython)).

## 2. Size limits

| Unit | Guideline | Hard limit (needs a `# WHY:` to exceed) |
|---|---|---|
| Module | ≤ 400 lines of code, *excluding* docstrings and comments | 600 |
| Function | ≤ 40 lines of code | 80 |
| Nesting depth | ≤ 3 levels of indentation inside a function | 4 |
| Function parameters | ≤ 5 | 7 (beyond that, pass a dataclass) |
| Line length | 100 characters (ruff) | — |

Small units matter more here than usual. Each function should fit on one screen *with* its comments, so that one code-tour stop can explain it.

## 3. Typing

- **Everything in `src/kiln` is fully annotated** and passes `mypy --strict` ([ADR-019](../decisions.md#adr-019)). Tests and labs annotate public helpers. Inside test bodies, annotations are optional.
- Use **built-in generics**: `list[str]`, `dict[str, int]`, `X | None` (not `Optional[X]`, not `typing.List`).
- **PEP 695 syntax** (`def first[T](xs: list[T]) -> T:` and `type JobId = str`) is allowed, because the floor is 3.13 and it's been valid since 3.12. Its Python note (`python/generics.md`) must exist before first use.
- **`Any` is forbidden** in `src/kiln` unless it comes from a JSON boundary. JSON values use one alias, `type JSONValue = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]`, defined once in `protocol/messages.py`.
- Use `typing.Protocol` for pluggable interfaces (`Executor`, `Launcher`). Don't use abstract base classes unless shared behaviour is needed.
- No `# type: ignore` without an error code and a `# WHY:` (`# type: ignore[attr-defined]  # WHY: _is_gil_enabled only exists on 3.13+ builds`).

## 4. Errors and exceptions

- **No bare `except:`** and no `except BaseException:` except at a process's outermost boundary (worker `main`, master loop catch-all), and there it **must** log with the traceback and re-raise or exit.
- `except Exception:` is allowed only where the spec says "never crash because of X" (for example the master loop's `internal_error` path in spec/06 §2.5, or OOM evidence reading in spec/04 §4.2). Each one has a `# WHY:` naming the spec section.
- Catch the **narrowest** exception that the call can actually raise: `BlockingIOError` for a non-blocking `recv`, `ChildProcessError` for `waitpid`, `ProcessLookupError` for `os.kill` on a gone pid. The comment names the syscall error it maps to (`EAGAIN`, `ECHILD`, `ESRCH`).
- **Kiln's own exceptions** live in the module that raises them and derive from `kiln.errors.KilnError`. `kiln/errors.py` is a tiny K1 module. Examples: `FrameTooLarge(KilnError)`, `ConfigError(KilnError)`.
- Never use exceptions for normal control flow in hot loops. The master loop checks return values (`recv` returning `b""` means EOF) rather than raising.
- `assert` is for **invariants** only (`# INVARIANT:` comment next to it), never for validating input. Asserts disappear with `python -O`.

## 5. Logging

- Use `logging.getLogger(__name__)`, one logger per module. Never use the root logger, and never `print` in `src/` (ruff `T20`). The CLI writes user output through `cli._out()`.
- Every log call is an **event** from the catalogue in [spec/06 §2](../spec/06-observability.md#2-event-catalogue-complete-add-new-ones-here-first): `log.info("worker_ready", extra={"kv": {...}})`, through the small helper `logsetup.event(log, level, name, **fields)`. Adding an event means adding it to spec/06 first.
- Use lazy formatting, because the helper only builds the line if the level is enabled. Never build strings with f-strings in DEBUG calls on hot paths.

## 6. Global state

- **No mutable module-level state** in `src/kiln`, with these documented exceptions:
  - the task registry *inside* an `App` instance (not global; the user owns the `App`)
  - logger objects
  - `kiln/labs/tasks.py` leak holders, tagged `# LAB:` and only in `kiln.labs`
  - per-process singletons that the spec requires: the worker's own `WorkerContext`, set once in `worker/main.py` with an `# INVARIANT: set exactly once per process` comment
- Configuration is passed **explicitly** (a `Config` object as a parameter), never read from a global.

## 7. Fork-safety rules

These rules exist because of [ADR-006](../decisions.md#adr-006) and the K2 lessons. Each one prevents a specific, real bug:

1. **The master never starts a thread**, not even indirectly. Some libraries start threads on import, and the master imports nothing from third parties (ADR-005). A unit test asserts `threading.active_count() == 1` in the master at the moment before every fork, and on 3.12+ we also treat Python's fork-with-threads `DeprecationWarning` as an **error** in tests (`-W error::DeprecationWarning` for the integration suite).
2. **After fork, the child's first actions are fixed and ordered** (spec/04 §3.1, §6): reset signals and the wakeup fd → close inherited fds → re-run `logsetup.configure` → only then touch the app.
3. **Don't hold a lock across a fork.** The master has no locks, by rule 1. Workers never fork.
4. **`os.register_at_fork`** is used for reinitialising state that must differ per child (for example per-process counters in `kiln.memory`). Every use has a `# CONCEPT(fork):` comment explaining which side (`before`, `after_in_parent`, `after_in_child`) and why.
5. **Never use `os.fork()` outside `launch/fork.py` and `launch/forkserver_main.py`.** The import-boundary test also greps for it.
6. **A forked child always ends with `os._exit(code)`.** The fork launcher calls worker main inside `try: … finally: os._exit(code)`. Without it, two things go wrong:
   - When worker main returns or raises, the child would **return into the master's code** right after the `os.fork()` call, and you'd suddenly have two masters.
   - `sys.exit()` would run `atexit` handlers registered by the *master* and flush stdio buffers copied from it, so output gets printed twice.

   Spawned workers are fresh interpreters, so they exit normally (`sys.exit(code)`) and run normal interpreter finalization. Only spawned workers are affected by K3's `worker.exit_via_os_exit` switch, which skips that finalization (Instagram's `atexit.register(os._exit, 0)` trick). Forked and forkserver workers already skip it because of this rule, and the K3 lab must say so. The `fork` primer explains all of this.

## 8. Resource cleanup

- Every file, socket and selector is managed with `with`, or explicitly closed in a `finally`. The owning class has a `close()` method that is **idempotent** (safe to call twice).
- Every fd the master creates is recorded in the **child-close registry** (spec/04 §6 rule 1) at creation time, and removed from it at close time. Use one helper, `connections.track_fd(sock, close_in_child=True)`.
- Every subprocess that tests or labs start is waited for or killed in a `finally` (spec/09 §4.5).
- Temporary files and directories go through `tempfile` with context managers.

## 9. Non-blocking I/O rules (master and gateway)

- Every socket in the master is `setblocking(False)` right after creation or `accept()`. That line has a `# WHY:` comment pointing to ADR-006.
- Use `recv_into` a preallocated buffer or `recv(65536)`, never `recv` in a loop "until done".
- Handle `BlockingIOError` (no data now), `b""` (peer closed), `ConnectionResetError` and `BrokenPipeError` explicitly, each with a comment.
- In the gateway, **never call a blocking function on the event loop thread.** Anything that might block goes through `asyncio.to_thread`. Exception: `POST /debug/block`, tagged `# LAB:`.

## 10. Imports

- Standard library first, then (in labs only) third-party, then Kiln. Absolute imports (`from kiln.protocol import framing`) everywhere except inside a package's own `__init__.py`. Ruff `I` sorts them.
- No `from x import *`.
- Version-gated imports go inside a function, behind a version check with a friendly error:

  ```python
  def make_interpreter_executor(...):
      if sys.version_info < (3, 14):
          raise ConfigError("worker.executor = 'interpreters' needs Python 3.14+ (running "
                            f"{platform.python_version()}). Use 'threads' or upgrade.")
      from kiln.worker.executors import interpreters  # PY: importing inside a function delays it until called
      ...
  ```

## 11. Comment tags

Covered in full in [`documentation.md` §4.3](documentation.md#43-comment-tags-grep-able-checked-). In short: `WHY:`, `PY:`, `CONCEPT(slug):`, `GOTCHA:`, `INCIDENT(id):`, `SYSCALL:`, `INVARIANT:`, `LAB:`.

## 12. Formatting and commits

- `ruff format` is the only formatter. Never hand-format against it.
- One step per commit (milestone-workflow §2f). The subject line is imperative and ≤ 72 chars, for example `Add FrameDecoder with incremental parsing`. The body explains *why*, in plain English, and links the spec section.
