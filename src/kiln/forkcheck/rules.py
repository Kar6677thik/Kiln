"""The fork-assumption rules FA001–FA008: code that only works because of the `fork` start method.

WHY THIS MODULE EXISTS
    Python 3.14 changed the Linux default start method from fork to forkserver, and real
    projects broke (krb5, oslo.concurrency, FSL pyfeeds; errata E-05..E-07). Each rule here
    encodes one of those failure patterns so it can be found by *reading* code, before it
    breaks in production. The rule catalogue is docs/milestones/K2-start-methods-forkcheck.md
    §5.7; each rule's docstring repeats its entry.

HOW IT FITS
    scanner.py ──for each ModuleInfo──▶ RULES[id](mod, project) ──yields──▶ Finding
    report.py prints the findings; confirm.py runs the code to see whether behaviour changes.

READ FIRST
    docs/concepts/start-methods.md, docs/concepts/pickling.md, docs/concepts/main-module.md,
    docs/concepts/static-vs-dynamic-analysis.md, docs/python/ast-module.md
    (each rule is a generator: it `yield`s findings one at a time)

MILESTONE
    Introduced in K2.
"""

import ast
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from kiln.forkcheck.symbols import FuncNode, ModuleInfo, ProcSite, anchor_for, is_fork_selection

if TYPE_CHECKING:
    from kiln.forkcheck.scanner import Project

SPAWNISH = ("spawn", "forkserver")
WORK_KINDS = ("pool_call", "executor_call")  # work sent to an existing pool, not a new process
ALL_METHODS = ("fork", "spawn", "forkserver")
LOG_METHODS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
)
CONFIG_CALLS = frozenset(
    {"logging.basicConfig", "logging.config.dictConfig", "logging.config.fileConfig"}
)
UNPICKLABLE_CALLS = frozenset(
    {
        "threading.Lock",
        "threading.RLock",
        "threading.Condition",
        "threading.Semaphore",
        "threading.BoundedSemaphore",
        "threading.Event",
        "open",
        "io.open",
        "socket.socket",
        "sqlite3.connect",
    }
)
MUTATING_METHODS = frozenset(
    {"update", "append", "extend", "add", "setdefault", "insert", "clear", "pop", "remove"}
)


@dataclass(frozen=True, slots=True)
class Finding:
    """One problem found by a rule (K2 spec §5.6).

    Why:
        A uniform record lets the report print text or JSON, sort, suppress and confirm
        findings without knowing which rule produced them.
    """

    rule: str
    name: str
    severity: str  # "error", "warning" or "info"
    path: str
    line: int
    col: int
    end_line: int
    message: str
    hint: str
    breaks_under: tuple[str, ...]
    motivation: str
    confirmation: dict[str, object] | None = field(default=None, compare=False)


def _finding(
    rule: str,
    mod: ModuleInfo,
    node: ast.AST,
    severity: str,
    message: str,
    hint: str,
    breaks: tuple[str, ...],
) -> Finding:
    """Build a Finding for a node, filling in the rule's name and motivation.

    Why:
        Every rule reports the same fields; building them in one place keeps names, motivations and
        positions consistent.

    Args:
        rule: the rule id.
        mod: the module.
        node: where it is.
        severity: error, warning or info.
        message: what and why.
        hint: how to fix.
        breaks: which start methods are affected.

    Returns:
        The Finding.
    """
    name, motivation = RULE_INFO[rule]
    line = getattr(node, "lineno", 1)
    return Finding(
        rule=rule,
        name=name,
        severity=severity,
        path=mod.display,
        line=line,
        col=getattr(node, "col_offset", 0) + 1,
        end_line=getattr(node, "end_lineno", None) or line,
        message=message,
        hint=hint,
        breaks_under=breaks,
        motivation=motivation,
    )


# --- shared helpers ------------------------------------------------------------------------------


def calls_in(node: ast.AST) -> list[ast.Call]:
    """Every call inside a node (including the node itself).

    Args:
        node: any node.

    Returns:
        The calls, in walk order.
    """
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def target_function(mod: ModuleInfo, site: ProcSite) -> FuncNode | None:
    """Find the module-level function a site's child will run, if it can be named.

    Why:
        FA003 and FA004 inspect what the child *does*; that needs the function's body. A Process
        subclass's child runs its `run` method.

    Args:
        mod: the module.
        site: the site.

    Returns:
        The function definition, or None if it can't be resolved in this module.
    """
    target = site.target
    if site.kind == "subclass" and isinstance(target, ast.Name):
        cls = mod.classes.get(target.id)
        if cls is not None:
            for stmt in cls.body:
                if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef) and stmt.name == "run":
                    return stmt
        return None
    if (
        isinstance(target, ast.Call)
        and mod.resolve(target.func) == "functools.partial"
        and target.args
    ):
        target = target.args[0]
    if isinstance(target, ast.Name):
        return mod.defs.get(target.id)
    return None


def _called_names(node: ast.AST) -> set[str]:
    """Names of plain functions called inside a node (`f(...)`).

    Args:
        node: any node.

    Returns:
        The called names.
    """
    return {c.func.id for c in calls_in(node) if isinstance(c.func, ast.Name)}


def _guard_nodes(mod: ModuleInfo) -> list[ast.stmt]:
    """The statements inside `if __name__ == "__main__":` bodies.

    Why:
        Several rules need 'what the parent runs under the guard' as a list of statements they can
        walk.

    Args:
        mod: the module.

    Returns:
        The statements.
    """
    found = []
    for node in ast.walk(mod.tree):
        if (
            isinstance(node, ast.stmt)
            and mod.in_guard(node.lineno)
            and not isinstance(node, ast.If)
        ):
            found.append(node)
    return found


def _reachable_from_guard(mod: ModuleInfo) -> set[str]:
    """Module functions called (directly or through each other) from the guard region.

    Why:
        Code reached from the guard runs only in the parent: a child re-importing the module
        never enters the guard.

    Args:
        mod: the module.

    Returns:
        Function names.
    """
    reached: set[str] = set()
    todo = [name for stmt in _guard_nodes(mod) for name in _called_names(stmt)]
    while todo:
        name = todo.pop()
        if name in reached or name not in mod.defs:
            continue
        reached.add(name)
        todo.extend(_called_names(mod.defs[name]))
    return reached


def _site_breaks(site: ProcSite) -> tuple[str, ...]:
    """Which start methods a pickling problem at this site affects.

    Args:
        site: the site.

    Returns:
        All three for pool/executor work (pickled even under fork), else spawn/forkserver.
    """
    return ALL_METHODS if site.sends_through_pickle_always else SPAWNISH


# --- FA001 ---------------------------------------------------------------------------------------


def check_fa001(mod: ModuleInfo, project: "Project") -> Iterator[Finding]:
    """FA001 unguarded-process-start (error). Motivated by krb5 t_otp.py (E-05).

    Detects a process-creation site (or a call to a same-module function that contains one,
    one level deep) that executes as a top-level statement: not inside a def/class body and
    not inside an `if __name__ == "__main__":` guard.

    Why:
        Why it breaks: spawn and forkserver children re-import the main script as `__mp_main__`;
        unguarded top-level code runs again in the child (multiprocessing raises "An attempt has
        been made to start a new process before the current process has finished its bootstrapping
        phase", or side effects repeat). Downgraded to info when the module sets
        `set_start_method("fork")` at top level before the site (FA008 reports that).

    Args:
        mod: the module.
        project: all scanned modules.

    Yields:
        Findings.
    """
    hint = (
        "Move process creation under `if __name__ == '__main__':` (see "
        "docs/concepts/main-module.md)."
    )
    funcs_with_sites = {
        site.func.name for site in mod.sites if site.func is not None and site.func.name in mod.defs
    }
    for site in mod.sites:
        if id(site.call) not in mod.toplevel_ids or site.kind in WORK_KINDS:
            continue
        forced = mod.fork_set_line is not None and mod.fork_set_line < site.line
        severity = "info" if forced or site.method == "fork" else "error"
        message = (
            "a process is started at module top level, outside `if __name__ == '__main__':`; a "
            "spawn/forkserver child re-imports this file and runs it again"
        )
        yield _finding("FA001", mod, site.call, severity, message, hint, SPAWNISH)
    for call in calls_in(mod.tree):
        if (
            id(call) in mod.toplevel_ids
            and isinstance(call.func, ast.Name)
            and call.func.id in funcs_with_sites
        ):
            forced = mod.fork_set_line is not None and mod.fork_set_line < call.lineno
            message = (
                f"'{call.func.id}()' starts processes and is called at module top level, outside "
                "the `__main__` guard"
            )
            yield _finding(
                "FA001", mod, call, "info" if forced else "error", message, hint, SPAWNISH
            )


# --- FA002 ---------------------------------------------------------------------------------------


def _unpicklable_target(mod: ModuleInfo, site: ProcSite, target: ast.expr) -> str | None:
    """Explain why a target can't be pickled by name, or return None if it can.

    Why:
        Each unpicklable shape (lambda, nested function, local class, partial) needs its own
        explanation in the message.

    Args:
        mod: the module.
        site: the site (its enclosing function decides what "nested" means).
        target: the target expression.

    Returns:
        A short description, or None.
    """
    if isinstance(target, ast.Lambda):
        return "a lambda has no importable name (its qualname is '<lambda>')"
    if (
        isinstance(target, ast.Call)
        and mod.resolve(target.func) == "functools.partial"
        and target.args
    ):
        inner = _unpicklable_target(mod, site, target.args[0])
        return f"functools.partial wraps {inner}" if inner else None
    func = site.func
    if func is None:
        return None
    if isinstance(target, ast.Name) and target.id in mod.nested_funcs.get(id(func), set()):
        captured = mod.frees.get(target.id, ())
        extra = (
            f", and it captures {', '.join(captured)} from the enclosing function"
            if captured
            else ""
        )
        return (
            f"nested function '{target.id}' has qualname "
            f"'{func.name}.<locals>.{target.id}', which can't be looked up by name{extra}"
        )
    if isinstance(target, ast.Attribute):
        base = target.value
        local_classes = mod.local_classes.get(id(func), set())
        if (
            isinstance(base, ast.Call)
            and isinstance(base.func, ast.Name)
            and base.func.id in local_classes
        ):
            return f"method of class '{base.func.id}', which is defined inside '{func.name}'"
        if isinstance(base, ast.Name):
            for node in ast.walk(func):
                if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == base.id for t in node.targets
                ):
                    value = node.value
                    if (
                        isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Name)
                        and value.func.id in local_classes
                    ):
                        return (
                            f"method of class '{value.func.id}', which is defined inside "
                            f"'{func.name}'"
                        )
    return None


def check_fa002(mod: ModuleInfo, project: "Project") -> Iterator[Finding]:
    """FA002 unpicklable-target (error). Motivated by oslo.concurrency (E-06).

    Detects a site whose target/callable is a lambda, a nested function (via symtable/AST:
    defined inside another function), a method of a class defined inside a function, or
    functools.partial of one of those.

    Why:
        Why it breaks: spawn and forkserver must pickle the target, and functions pickle by
        qualified name (`module.qualname`); `outer.<locals>.inner` can't be looked up. Under fork a
        plain Process pickles nothing, which is why the bug hid. Pool.map/Executor.submit pickle the
        callable even under fork, so those fail under all three methods. A site with an explicit
        get_context("fork") receiver is reported at info.

    Args:
        mod: the module.
        project: all scanned modules.

    Yields:
        Findings.
    """
    for site in mod.sites:
        if site.target is None or site.kind == "subclass":
            continue
        why = _unpicklable_target(mod, site, site.target)
        if why is None:
            continue
        breaks = _site_breaks(site)
        severity = (
            "info" if site.method == "fork" and not site.sends_through_pickle_always else "error"
        )
        # CONCEPT(pickling): functions are pickled as a reference "module.qualname", not as code.
        message = f"the child's target must be pickled, but {why}"
        yield _finding(
            "FA002",
            mod,
            anchor_for(mod, site),
            severity,
            message,
            "move it to module level (a plain top-level def)",
            breaks,
        )


# --- FA003 ---------------------------------------------------------------------------------------


def _config_calls(mod: ModuleInfo, node: ast.AST) -> list[ast.Call]:
    """Logging-configuration calls inside a node.

    Why:
        FA003 must know where logging is configured, to decide whether the configuration only
        happens in the parent.

    Args:
        mod: the module.
        node: any node.

    Returns:
        The calls (basicConfig, dictConfig, fileConfig, addHandler, getLogger().setLevel).
    """
    found = []
    for call in calls_in(node):
        name = mod.resolve(call.func)
        if name in CONFIG_CALLS or (
            isinstance(call.func, ast.Attribute) and call.func.attr == "addHandler"
        ):
            found.append(call)
        elif isinstance(call.func, ast.Attribute) and call.func.attr == "setLevel":
            receiver = call.func.value
            if isinstance(receiver, ast.Call) and mod.resolve(receiver.func) == "logging.getLogger":
                found.append(call)
    return found


def _logger_names(mod: ModuleInfo) -> set[str]:
    """Variables bound to logging.getLogger(...) anywhere in the module.

    Why:
        `log = logging.getLogger(__name__)` is how most code logs; the rule must recognise
        `log.info(...)` as logging.

    Args:
        mod: the module.

    Returns:
        The names.
    """
    names: set[str] = set()
    for node in ast.walk(mod.tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and mod.resolve(node.value.func) == "logging.getLogger"
        ):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _logs(mod: ModuleInfo, func: FuncNode, loggers: set[str]) -> bool:
    """Whether a function (or a module function it calls) emits log records.

    Why:
        FA003 only matters if the child actually logs, here or one call deeper.

    Args:
        mod: the module.
        func: the function.
        loggers: names bound to loggers.

    Returns:
        True if it logs.
    """
    bodies: list[FuncNode] = [func] + [mod.defs[n] for n in _called_names(func) if n in mod.defs]
    for body in bodies:
        for call in calls_in(body):
            if not isinstance(call.func, ast.Attribute) or call.func.attr not in LOG_METHODS:
                continue
            receiver = call.func.value
            if mod.resolve(receiver) == "logging" or (
                isinstance(receiver, ast.Name) and receiver.id in loggers
            ):
                return True
    return False


def _configures(mod: ModuleInfo, node: ast.AST, seen: set[str]) -> bool:
    """Whether code configures logging itself, or uses a QueueHandler (directly or via calls).

    Why:
        A target (or initializer) that configures logging itself is the fix, so FA003 must not fire
        on it.

    Args:
        mod: the module.
        node: a function or expression.
        seen: names already followed (prevents loops).

    Returns:
        True if logging is set up there.
    """
    if _config_calls(mod, node):
        return True
    for sub in ast.walk(node):
        name = mod.resolve(sub) if isinstance(sub, ast.Name | ast.Attribute) else None
        if name and name.endswith("QueueHandler"):
            return True
    for called in _called_names(node):
        if called in mod.defs and called not in seen:
            seen.add(called)
            if _configures(mod, mod.defs[called], seen):
                return True
    return False


def check_fa003(mod: ModuleInfo, project: "Project") -> Iterator[Finding]:
    """FA003 parent-only-logging (warning). Motivated by FSL pyfeeds (E-07).

    Detects a module where (a) logging is configured only inside the `__main__` guard or in
    functions reachable from it, (b) a site's target (resolved in this module) emits log
    records, and (c) neither the target nor a Pool initializer configures logging (a
    QueueHandler reference counts as configuring).

    Why:
        Why it breaks: handlers are objects in the parent's memory. Spawn and forkserver children
        start with none, so records go to the "last resort" handler (WARNING+ only, unformatted) or
        vanish. Logging configured by a framework at import time can't be seen; suppress with `#
        forkcheck: ignore[FA003]`.

    Args:
        mod: the module.
        project: all scanned modules.

    Yields:
        Findings.
    """
    configs = _config_calls(mod, mod.tree)
    if not configs or any(id(c) in mod.toplevel_ids for c in configs):
        return
    reachable = _reachable_from_guard(mod)
    parent_only = [c for c in configs if mod.in_guard(c.lineno) or _in_reached(mod, c, reachable)]
    if not parent_only:
        return
    loggers = _logger_names(mod)
    for site in mod.sites:
        target = target_function(mod, site)
        if target is None or not _logs(mod, target, loggers):
            continue
        if _configures(mod, target, set()):
            continue
        initializer = site.initializer
        if initializer is None and site.kind in ("pool_call", "executor_call"):
            initializer = _initializer_for_receiver(mod, site)
        if (
            initializer is not None
            and isinstance(initializer, ast.Name)
            and initializer.id in mod.defs
        ) and _configures(mod, mod.defs[initializer.id], set()):
            continue
        message = (
            f"'{target.name}' logs, but logging is only configured in the parent "
            f"(line {parent_only[0].lineno}); spawn/forkserver children start without "
            "those handlers"
        )
        hint = (
            "configure logging inside the child (in the target or a Pool initializer=), "
            "or use a QueueHandler"
        )
        yield _finding("FA003", mod, anchor_for(mod, site), "warning", message, hint, SPAWNISH)


def _in_reached(mod: ModuleInfo, node: ast.AST, reached: set[str]) -> bool:
    """Whether `node` sits in a function the guard reaches.

    Args:
        mod: the module.
        node: a node.
        reached: names of functions reachable from the guard.

    Returns:
        True if the enclosing function is in `reached`.
    """
    enclosing = mod.enclosing.get(id(node))
    return enclosing is not None and enclosing.name in reached


def _initializer_for_receiver(mod: ModuleInfo, site: ProcSite) -> ast.expr | None:
    """For pool.map(...), find the initializer= of the Pool that created `pool`.

    Why:
        pool.map(...) inherits the Pool's initializer=, which lives on the Pool(...) call, not on
        the map call.

    Args:
        mod: the module.
        site: a pool_call/executor_call site.

    Returns:
        The initializer expression, or None.
    """
    func = site.call.func
    if not isinstance(func, ast.Attribute):
        return None
    receiver = func.value
    if isinstance(receiver, ast.Call):
        return next((kw.value for kw in receiver.keywords if kw.arg == "initializer"), None)
    if isinstance(receiver, ast.Name):
        for other in mod.sites:
            if other.kind in ("pool", "executor") and other.initializer is not None:
                return other.initializer
    return None


# --- FA004 ---------------------------------------------------------------------------------------


def _mutators(mod: ModuleInfo, name: str) -> set[str]:
    """Module functions that rebind (`global`) or mutate a module-level name.

    Why:
        FA004 needs the functions that change a global after import time.

    Args:
        mod: the module.
        name: the global's name.

    Returns:
        Function names.
    """
    found = set(mod.global_writers.get(name, set()))
    for fname, func in mod.defs.items():
        for node in ast.walk(func):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AugAssign):
                targets = [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Subscript | ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == name
                ):
                    found.add(fname)
            if (
                (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in MUTATING_METHODS
                )
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == name
            ):
                found.add(fname)
    return found


def _called_before(mod: ModuleInfo, site: ProcSite, funcs: set[str]) -> str | None:
    """Whether any of `funcs` is called in the parent before the site runs.

    Why:
        A global changed only *after* the child starts can't be the cause; only calls that run
        before the site count.

    Args:
        mod: the module.
        site: the site.
        funcs: candidate mutator functions.

    Returns:
        The first such function name, or None.
    """
    scope: ast.AST | None = site.func
    if scope is not None:
        for call in calls_in(scope):
            if (
                isinstance(call.func, ast.Name)
                and call.func.id in funcs
                and call.lineno < site.line
            ):
                return call.func.id
    # (line, called name) for each plain-name call under the guard.
    guard_calls = [
        (c.lineno, c.func.id)
        for stmt in _guard_nodes(mod)
        for c in calls_in(stmt)
        if isinstance(c.func, ast.Name)
    ]
    site_calls = [
        line for line, name in guard_calls if site.func is not None and name == site.func.name
    ]
    limit = min(site_calls) if site_calls else site.line
    for line, name in guard_calls:
        if name in funcs and line < limit:
            return name
    return None


def check_fa004(mod: ModuleInfo, project: "Project") -> Iterator[Finding]:
    """FA004 runtime-global-not-inherited (warning).

    Motivated by "state not inherited" (oslo, research §00).

    Detects a site target (resolved in this module) that reads a module-level name G, where G is
    rebound via `global G` or mutated (`G[k] = v`, `G.attr = v`, `G.update(...)`) inside a
    function, and that function is called in the parent before the site (earlier in the same
    function, or in the guard region before it).

    Why:
        Why it breaks: spawn and forkserver children re-import the module and see G's import-time
        value, not the value the parent assigned later. A mutation that happens to restore the
        import-time value can't be told apart statically (accepted false positive).

    Args:
        mod: the module.
        project: all scanned modules.

    Yields:
        Findings.
    """
    for site in mod.sites:
        target = target_function(mod, site)
        if target is None:
            continue
        local_names = {a.arg for a in target.args.args} | {
            n.id
            for n in ast.walk(target)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
        }
        reads = {
            n.id
            for n in ast.walk(target)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        }
        for name in sorted(
            (reads & mod.module_names)
            - local_names
            - set(mod.defs)
            - set(mod.classes)
            - set(mod.aliases)
        ):
            changer = _called_before(mod, site, _mutators(mod, name) - {target.name})
            if changer is None:
                continue
            message = (
                f"'{target.name}' reads the global '{name}', which '{changer}()' changes in the "
                "parent before the process starts; a spawn/forkserver child sees the import-time "
                "value"
            )
            hint = f"pass '{name}' to the child as an argument instead"
            yield _finding("FA004", mod, anchor_for(mod, site), "warning", message, hint, SPAWNISH)


# --- FA005 ---------------------------------------------------------------------------------------


def _side_effects(mod: ModuleInfo) -> list[tuple[ast.AST, str]]:
    """Top-level statements with import-time side effects (K2 spec §5.7 FA005 list).

    Why:
        FA005 needs every import-time side effect of a module, with its line, before deciding
        whether a process starter reaches the module.

    Args:
        mod: the module.

    Returns:
        (node, description) pairs.
    """
    found: list[tuple[ast.AST, str]] = []
    thread_vars: set[str] = set()
    socket_vars: set[str] = set()
    for node in ast.walk(mod.tree):
        if id(node) not in mod.toplevel_ids:
            continue
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            created = mod.resolve(node.value.func)
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if created == "threading.Thread":
                thread_vars |= names
            if created == "socket.socket":
                socket_vars |= names
        if isinstance(node, ast.Assign) and any(
            mod.resolve(t) == "sys.excepthook" for t in node.targets
        ):
            found.append((node, "sets sys.excepthook"))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
            mod.resolve(d) == "atexit.register" for d in node.decorator_list
        ):
            found.append((node, "registers an atexit handler (decorator)"))
    for node in ast.walk(mod.tree):
        if not isinstance(node, ast.Call) or id(node) not in mod.toplevel_ids:
            continue
        description = _call_side_effect(mod, node, thread_vars, socket_vars)
        if description:
            found.append((node, description))
    return found


def _call_side_effect(
    mod: ModuleInfo, call: ast.Call, thread_vars: set[str], socket_vars: set[str]
) -> str | None:
    """Describe a call's import-time side effect, or return None.

    Why:
        Each side-effect API has its own shape (a call, a method on a bound variable, an open mode),
        so they are recognised one by one.

    Args:
        mod: the module.
        call: a top-level call.
        thread_vars: names bound to threading.Thread(...).
        socket_vars: names bound to socket.socket(...).

    Returns:
        A description or None.
    """
    name = mod.resolve(call.func)
    simple = {
        "atexit.register": "registers an atexit handler",
        "signal.signal": "installs a signal handler",
        "os.fork": "calls os.fork()",
        "logging.basicConfig": "configures logging",
    }
    if name in simple:
        return simple[name]
    if isinstance(call.func, ast.Attribute):
        receiver = call.func.value
        made = mod.resolve(receiver.func) if isinstance(receiver, ast.Call) else None
        is_thread = made == "threading.Thread" or (
            isinstance(receiver, ast.Name) and receiver.id in thread_vars
        )
        is_socket = made == "socket.socket" or (
            isinstance(receiver, ast.Name) and receiver.id in socket_vars
        )
        if call.func.attr == "start" and is_thread:
            return "starts a thread"
        if call.func.attr == "bind" and is_socket:
            return "binds a socket"
    if isinstance(call.func, ast.Name) and call.func.id == "open":
        mode = (
            call.args[1]
            if len(call.args) > 1
            else next((k.value for k in call.keywords if k.arg == "mode"), None)
        )
        if (
            isinstance(mode, ast.Constant)
            and isinstance(mode.value, str)
            and ("w" in mode.value or "a" in mode.value)
        ):
            return "opens a file for writing"
    return None


def check_fa005(mod: ModuleInfo, project: "Project") -> Iterator[Finding]:
    """FA005 import-time-side-effect (warning). Motivated by krb5's k5test atexit handler (E-05).

    Detects, in a module that starts processes or is imported (transitively, within the scanned
    roots) by one, a top-level statement (outside guards) that calls atexit.register,
    signal.signal, threading.Thread(...).start(), os.fork, logging.basicConfig,
    socket.socket(...).bind or open(..., "w"/"a"), or assigns sys.excepthook.

    Why:
        Why it matters: under spawn and forkserver the child re-imports these modules and repeats
        the side effect. krb5's k5test registered an atexit handler on import, so every child
        printed a bogus "re-run this test" banner. Reported on the side-effect line, naming the
        import chain. Idempotent side effects are reported anyway.

    Args:
        mod: the module.
        project: all scanned modules.

    Yields:
        Findings.
    """
    # CONCEPT(import-system): a module's top-level code runs once per process that imports it.
    starters = project.starters_reaching(mod)
    if not starters:
        return
    starter, site_line = starters[0]
    if starter is mod:
        context = (
            f"this file starts processes (line {site_line}), so spawn/forkserver children "
            "re-import it"
        )
    else:
        context = (
            f"imported by {starter.display}, which starts processes (line {site_line}); "
            "spawn/forkserver children re-import it"
        )
    for node, description in _side_effects(mod):
        message = f"top-level code {description}; {context} and repeat this"
        hint = "move it into a function called under `if __name__ == '__main__':`"
        yield _finding("FA005", mod, node, "warning", message, hint, SPAWNISH)


# --- FA006 ---------------------------------------------------------------------------------------


def _is_patch(mod: ModuleInfo, node: ast.AST) -> bool:
    """Whether an expression is unittest.mock.patch / patch.object / patch.dict (or a call to one).

    Args:
        mod: the module.
        node: an expression.

    Returns:
        True for a mock patch.
    """
    expr = node.func if isinstance(node, ast.Call) else node
    name = mod.resolve(expr) or ""
    return name.startswith(("unittest.mock.patch", "mock.patch"))


def _active_patch(mod: ModuleInfo, site: ProcSite) -> str | None:
    """Describe a patch that is in effect at the site's line, or return None.

    Why:
        A patch only matters if it is still in effect at the site line; each patch style (decorator,
        with, start(), monkeypatch, assignment) ends differently.

    Args:
        mod: the module.
        site: the site.

    Returns:
        A description, or None.
    """
    func = site.func
    if func is None:
        return None
    if any(_is_patch(mod, d) for d in func.decorator_list):
        return f"'{func.name}' is decorated with a mock patch"
    cls = mod.enclosing_class.get(id(site.call))
    if cls is not None and any(_is_patch(mod, d) for d in cls.decorator_list):
        return f"class '{cls.name}' is decorated with a mock patch"
    patch_vars: set[str] = set()
    for node in ast.walk(func):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.With | ast.AsyncWith) and any(
            _is_patch(mod, item.context_expr) for item in node.items
        ):
            end = node.end_lineno or node.lineno
            if node.lineno <= site.line <= end:
                return f"the call is inside `with patch(...)` (line {node.lineno})"
        if line >= site.line:
            continue
        if isinstance(node, ast.Assign) and _is_patch(mod, node.value):
            patch_vars.update(t.id for t in node.targets if isinstance(t, ast.Name))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            receiver = node.func.value
            if (
                node.func.attr == "start"
                and isinstance(receiver, ast.Name)
                and receiver.id in patch_vars
            ):
                return f"patcher '{receiver.id}' was started (line {line})"
            if (
                node.func.attr == "setattr"
                and isinstance(receiver, ast.Name)
                and receiver.id == "monkeypatch"
            ):
                return f"monkeypatch.setattr was called (line {line})"
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in mod.aliases
                ):
                    return f"'{target.value.id}.{target.attr}' was replaced (line {line})"
    return None


def check_fa006(mod: ModuleInfo, project: "Project") -> Iterator[Finding]:
    """FA006 parent-patch-not-inherited (warning). Motivated by git-repo (E-08).

    Detects, inside a function containing a site (including test functions/classes decorated
    with mock.patch*), a patch still in effect at the site: a unittest.mock patch used as a
    decorator, as a `with` context, or via `.start()`; `monkeypatch.setattr(...)`; or a direct
    assignment to an attribute of an imported module or class.

    Why:
        Why it breaks: a patch modifies objects in *this* process; spawn and forkserver children
        import fresh, unpatched modules. git-repo hit exactly this with a class-level mock.

    Args:
        mod: the module.
        project: all scanned modules.

    Yields:
        Findings.
    """
    for site in mod.sites:
        if site.kind in WORK_KINDS:
            continue  # WHY: reported once, on the Pool(...) that creates the processes
        patch = _active_patch(mod, site)
        if patch is None:
            continue
        message = (
            f"{patch}; the patch lives only in this process, and a spawn/forkserver child sees the "
            "original"
        )
        hint = "apply the patch inside the child (in the target), or pass the value as an argument"
        yield _finding("FA006", mod, site.call, "warning", message, hint, SPAWNISH)


# --- FA007 ---------------------------------------------------------------------------------------


def _unpicklable_expr(mod: ModuleInfo, expr: ast.expr) -> str | None:
    """Describe an expression that creates an unpicklable object, or return None.

    Why:
        FA007 must say *what* can't be pickled, so the message can suggest the right replacement.

    Args:
        mod: the module.
        expr: an expression.

    Returns:
        A description or None.
    """
    if isinstance(expr, ast.Lambda):
        return "a lambda"
    if isinstance(expr, ast.GeneratorExp):
        return "a generator"
    if isinstance(expr, ast.Call):
        name = mod.resolve(expr.func)
        if name in UNPICKLABLE_CALLS:
            return f"a {name}() object"
        if isinstance(expr.func, ast.Name) and expr.func.id in mod.generator_funcs:
            return f"a generator from {expr.func.id}()"
    return None


def _bound_unpicklables(mod: ModuleInfo, site: ProcSite) -> dict[str, str]:
    """Names bound before the site (same function, or module scope) to unpicklable objects.

    Why:
        Most unpicklable arguments are created a few lines earlier and passed by name, so earlier
        assignments in the same function are tracked.

    Args:
        mod: the module.
        site: the site.

    Returns:
        {name: description}.
    """
    scope: ast.AST = site.func if site.func is not None else mod.tree
    bound: dict[str, str] = {}
    for node in ast.walk(scope):
        if getattr(node, "lineno", site.line) >= site.line:
            continue
        if isinstance(node, ast.Assign):
            why = _unpicklable_expr(mod, node.value)
            if why:
                bound.update({t.id: why for t in node.targets if isinstance(t, ast.Name)})
        if isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                why = _unpicklable_expr(mod, item.context_expr)
                if why and isinstance(item.optional_vars, ast.Name):
                    bound[item.optional_vars.id] = why
    return bound


def check_fa007(mod: ModuleInfo, project: "Project") -> Iterator[Finding]:
    """FA007 unpicklable-argument (error). Motivated by general experience.

    Detects an argument sent to a child (Process args=/kwargs=, Pool/Executor call arguments)
    that is a lock/RLock/Condition/Semaphore/Event, an open file, a socket, a sqlite3
    connection, a generator (expression or generator function call) or a lambda, either
    inline or via a name bound earlier in the same function (including `with open(...) as f`).

    Why:
        Why it breaks: these objects can't be pickled (`TypeError: cannot pickle '_thread.lock'
        object`). Process arguments are only pickled under spawn/forkserver; Pool and Executor
        arguments are pickled under all three. multiprocessing.Lock() is fine for Process args.

    Args:
        mod: the module.
        project: all scanned modules.

    Yields:
        Findings.
    """
    for site in mod.sites:
        if site.method == "fork" and not site.sends_through_pickle_always:
            continue
        bound = _bound_unpicklables(mod, site)
        for arg in site.child_args:
            why = _unpicklable_expr(mod, arg)
            if why is None and isinstance(arg, ast.Name):
                why = bound.get(arg.id)
            if why is None:
                continue
            breaks = _site_breaks(site)
            message = f"{why} is sent to the child, but it can't be pickled"
            hint = (
                "create it inside the child, or use a multiprocessing equivalent "
                "(multiprocessing.Lock, a Manager)"
            )
            yield _finding("FA007", mod, anchor_for(mod, site), "error", message, hint, breaks)


# --- FA008 ---------------------------------------------------------------------------------------


def check_fa008(mod: ModuleInfo, project: "Project") -> Iterator[Finding]:
    """FA008 explicit-fork-reliance (info). Motivated by the krb5 fix and pexpect (E-05, E-08).

    Detects set_start_method("fork"), get_context("fork") and os.fork().

    Why:
        Why report it: not a bug today, but fork is unsafe with threads (3.12+ DeprecationWarning)
        and is no longer the default anywhere, so this code *depends on* fork. It's also how krb5
        and pexpect "fixed" their breakage. Counted separately in calibration.

    Args:
        mod: the module.
        project: all scanned modules.

    Yields:
        Findings.
    """
    for call in calls_in(mod.tree):
        if is_fork_selection(mod, call) or mod.resolve(call.func) == "os.fork":
            message = (
                "this code explicitly relies on the fork start method, which is unsafe with "
                "threads and no longer the default"
            )
            yield _finding(
                "FA008",
                mod,
                call,
                "info",
                message,
                "prefer spawn/forkserver-safe code; see docs/concepts/start-methods.md",
                (),
            )


RULE_INFO: dict[str, tuple[str, str]] = {
    "FA000": ("parse-error", "scanner"),
    "FA001": ("unguarded-process-start", "krb5 t_otp.py (errata E-05)"),
    "FA002": ("unpicklable-target", "oslo.concurrency (errata E-06)"),
    "FA003": ("parent-only-logging", "FSL pyfeeds (errata E-07)"),
    "FA004": ("runtime-global-not-inherited", "state not inherited (research §00)"),
    "FA005": ("import-time-side-effect", "krb5 k5test atexit (errata E-05)"),
    "FA006": ("parent-patch-not-inherited", "git-repo (errata E-08)"),
    "FA007": ("unpicklable-argument", "general"),
    "FA008": ("explicit-fork-reliance", "krb5 fix, pexpect (errata E-05, E-08)"),
}

# WHY: an explicit table from id to rule function (code-style §1).
RULES: dict[str, Callable[[ModuleInfo, "Project"], Iterator[Finding]]] = {
    "FA001": check_fa001,
    "FA002": check_fa002,
    "FA003": check_fa003,
    "FA004": check_fa004,
    "FA005": check_fa005,
    "FA006": check_fa006,
    "FA007": check_fa007,
    "FA008": check_fa008,
}
