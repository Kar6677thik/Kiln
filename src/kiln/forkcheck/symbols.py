"""What the fork-assumption checker knows about one Python file: imports, functions, guards, sites.

WHY THIS MODULE EXISTS
    Every forkcheck rule asks questions like "is this call at the top of the module or inside
    `if __name__ == '__main__':`?", "is this function nested inside another one?" or "does
    `mp.Process` here really mean `multiprocessing.Process`?". This module answers them once per
    file, by reading the file's syntax tree (`ast`) and its scope table (`symtable`), so the
    rules in rules.py stay short. Nothing here runs the code: it is static analysis.

HOW IT FITS
    scanner.py ──build_module(path, source)──▶ ModuleInfo
        ModuleInfo.aliases        "mp" → "multiprocessing"
        ModuleInfo.guard_ranges   line ranges of `if __name__ == "__main__":` bodies
        ModuleInfo.toplevel_ids   every node that runs at import time (outside defs and guards)
        ModuleInfo.sites          every place a process is created (ProcSite)
    rules.py ──reads ModuleInfo──▶ findings

READ FIRST
    docs/concepts/ast-and-symtable.md, docs/concepts/main-module.md,
    docs/concepts/start-methods.md, docs/python/ast-module.md, docs/python/symtable-module.md

MILESTONE
    Introduced in K2.
"""

import ast
import symtable
from dataclasses import dataclass, field
from pathlib import Path

# The process-creating APIs of K2 spec §5.6 step 4, by fully qualified name.
PROCESS_NAMES = frozenset(
    {
        "multiprocessing.Process",
        "multiprocessing.context.Process",
        "multiprocessing.context.ForkProcess",
        "multiprocessing.context.SpawnProcess",
        "multiprocessing.context.ForkServerProcess",
    }
)
POOL_NAMES = frozenset({"multiprocessing.Pool", "multiprocessing.pool.Pool"})
EXECUTOR_NAMES = frozenset(
    {"concurrent.futures.ProcessPoolExecutor", "concurrent.futures.process.ProcessPoolExecutor"}
)
MANAGER_NAMES = frozenset({"multiprocessing.Manager"})
POOL_METHODS = frozenset(
    {
        "map",
        "imap",
        "imap_unordered",
        "starmap",
        "apply",
        "apply_async",
        "map_async",
        "starmap_async",
    }
)
EXECUTOR_METHODS = frozenset({"submit", "map"})

FuncNode = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True, slots=True)
class ProcSite:
    """One place in the code where a new process is created (or work is sent to one).

    Why:
        Every rule starts from these sites: "what runs in the child, with which arguments, and
        under which start method?" (K2 spec §5.6 step 4).
    """

    call: ast.Call
    kind: str  # process, pool, pool_call, executor, executor_call, manager, subclass, custom
    func: FuncNode | None  # the function the call is written in, or None for module level
    method: str | None  # "fork"/"spawn"/"forkserver" when a get_context(...) receiver says so
    target: ast.expr | None  # what the child runs (Process target=, Pool.map's first argument)
    child_args: tuple[ast.expr, ...]  # values that must be sent (pickled) to the child
    initializer: ast.expr | None  # Pool(initializer=...)

    @property
    def line(self) -> int:
        """The site's line number.

        Returns:
            The call's first line.
        """
        return self.call.lineno

    @property
    def sends_through_pickle_always(self) -> bool:
        """Whether this site pickles its callable even under fork (Pool and executors).

        Why:
            Pool and ProcessPoolExecutor send every task through a queue, so the callable is
            pickled under *every* start method; a plain Process under fork inherits memory.

        Returns:
            True for pool/executor work submissions.
        """
        return self.kind in ("pool_call", "executor_call")


@dataclass(slots=True)
class ModuleInfo:
    """Everything forkcheck learned about one file.

    Why:
        Built once per file and shared by all rules, so each rule reads facts instead of
        re-walking the syntax tree.
    """

    path: Path
    display: str
    source: str
    tree: ast.Module
    lines: list[str]
    aliases: dict[str, str] = field(default_factory=dict)
    defs: dict[str, FuncNode] = field(default_factory=dict)
    classes: dict[str, ast.ClassDef] = field(default_factory=dict)
    process_subclasses: set[str] = field(default_factory=set)
    guard_ranges: list[tuple[int, int]] = field(default_factory=list)
    toplevel_ids: set[int] = field(default_factory=set)
    enclosing: dict[int, FuncNode | None] = field(default_factory=dict)
    enclosing_class: dict[int, ast.ClassDef | None] = field(default_factory=dict)
    nested_funcs: dict[int, set[str]] = field(default_factory=dict)
    local_classes: dict[int, set[str]] = field(default_factory=dict)
    frees: dict[str, tuple[str, ...]] = field(default_factory=dict)
    global_writers: dict[str, set[str]] = field(default_factory=dict)
    module_names: set[str] = field(default_factory=set)
    ctx_vars: dict[str, str | None] = field(default_factory=dict)
    pool_vars: dict[str, str] = field(default_factory=dict)
    pool_ctors: dict[str, ast.Call] = field(default_factory=dict)  # var -> the Pool(...) call
    subclass_vars: dict[str, ast.Call] = field(default_factory=dict)  # var -> Subclass(...) call
    generator_funcs: set[str] = field(default_factory=set)
    fork_set_line: int | None = None
    imports: list[tuple[str, int]] = field(default_factory=list)  # (dotted name, level)
    sites: list[ProcSite] = field(default_factory=list)

    def resolve(self, node: ast.AST) -> str | None:
        """Turn `mp.Process` or `P` into a dotted name such as "multiprocessing.Process".

        Why:
            The same API can be written many ways (`import multiprocessing as mp`,
            `from multiprocessing import Process as P`); rules compare full names only.

        Args:
            node: an expression (usually a call's function).

        Returns:
            The dotted name, or None for things that aren't plain names or attributes.
        """
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self.resolve(node.value)
            return f"{base}.{node.attr}" if base else None
        return None

    def in_guard(self, line: int) -> bool:
        """Whether a line is inside an `if __name__ == "__main__":` body.

        Args:
            line: a line number.

        Returns:
            True inside a guard.
        """
        return any(start <= line <= end for start, end in self.guard_ranges)


def is_main_guard(test: ast.expr) -> bool:
    """Recognise `__name__ == "__main__"` (in either order, `==` only).

    Why:
        The guard is what keeps code from re-running when a spawned child re-imports the main
        script (docs/concepts/main-module.md).

    Args:
        test: an `if` statement's condition.

    Returns:
        True for the main-module guard.
    """
    if (
        not isinstance(test, ast.Compare)
        or len(test.ops) != 1
        or not isinstance(test.ops[0], ast.Eq)
    ):
        return False
    sides = [test.left, test.comparators[0]]
    has_name = any(isinstance(s, ast.Name) and s.id == "__name__" for s in sides)
    has_main = any(isinstance(s, ast.Constant) and s.value == "__main__" for s in sides)
    return has_name and has_main


def _collect_toplevel(stmts: list[ast.stmt], info: ModuleInfo) -> None:
    """Record every node that runs at import time, and the guard regions.

    Why:
        "Top-level" means: runs when the module is imported. That's module-body code outside
        guards, including code inside top-level if/try/with/for/while, but NOT the bodies of
        functions and classes (defining them runs nothing). Decorators do run at import time.

    Args:
        stmts: a list of statements at module level (or inside a top-level compound statement).
        info: the module info to fill in.
    """
    for stmt in stmts:
        if isinstance(stmt, ast.If) and is_main_guard(stmt.test):
            # CONCEPT(main-module): this body does NOT run when a child re-imports the module.
            start = stmt.body[0].lineno
            end = stmt.body[-1].end_lineno or stmt.body[-1].lineno
            info.guard_ranges.append((start, end))
            _collect_toplevel(stmt.orelse, info)
            continue
        stack: list[ast.AST] = [stmt]
        while stack:
            node = stack.pop()
            info.toplevel_ids.add(id(node))
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                stack.extend(node.decorator_list)  # WHY: decorators run when the def runs
                continue
            if isinstance(node, ast.Lambda):
                continue
            if isinstance(node, ast.If | ast.Try | ast.With | ast.For | ast.While):
                # WHY: recurse statement by statement so a nested guard is still recognised.
                for child in ast.iter_child_nodes(node):
                    if not isinstance(child, ast.stmt):
                        stack.append(child)
                for name in ("body", "orelse", "finalbody"):
                    body = getattr(node, name, None)
                    if body:
                        _collect_toplevel(body, info)
                for handler in getattr(node, "handlers", []):
                    _collect_toplevel(handler.body, info)
                continue
            stack.extend(ast.iter_child_nodes(node))


def _collect_aliases(tree: ast.Module, info: ModuleInfo) -> None:
    """Fill the import alias table and the list of imported module names.

    Why:
        `import multiprocessing as mp` and `from multiprocessing import Process as P` must both be
        recognised as the same API.

    Args:
        tree: the parsed module.
        info: the module info to fill in.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                info.aliases[local] = alias.name if alias.asname else alias.name.split(".")[0]
                info.imports.append((alias.name, 0))
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            info.imports.append((base, node.level))
            for alias in node.names:
                full = f"{base}.{alias.name}" if base else alias.name
                info.aliases[alias.asname or alias.name] = full
                if node.level or base:
                    info.imports.append((full, node.level))


def _collect_scopes(tree: ast.Module, info: ModuleInfo) -> None:
    """Map every node to its enclosing function and class, and record definitions.

    Why:
        Rules need "which function is this call in?" and "which names are nested functions or
        local classes there?" (a nested function can't be pickled by name).

    Args:
        tree: the parsed module.
        info: the module info to fill in.
    """
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            info.defs[stmt.name] = stmt
        elif isinstance(stmt, ast.ClassDef):
            info.classes[stmt.name] = stmt
    stack: list[tuple[ast.AST, FuncNode | None, ast.ClassDef | None]] = [(tree, None, None)]
    while stack:
        node, func, cls = stack.pop()
        info.enclosing[id(node)] = func
        info.enclosing_class[id(node)] = cls
        for child in ast.iter_child_nodes(node):
            child_func, child_cls = func, cls
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                if func is not None:
                    info.nested_funcs.setdefault(id(func), set()).add(child.name)
                if any(isinstance(n, ast.Yield | ast.YieldFrom) for n in ast.walk(child)):
                    info.generator_funcs.add(child.name)
                child_func = child
            elif isinstance(child, ast.ClassDef):
                if func is not None:
                    info.local_classes.setdefault(id(func), set()).add(child.name)
                child_cls = child
            stack.append((child, child_func, child_cls))


def _collect_symtable(info: ModuleInfo) -> None:
    """Use symtable for free variables and `global` declarations.

    Why:
        `symtable` is CPython's own scope analysis: it knows which names a nested function
        captures (frees) and which names a function declares `global` and assigns, which is
        exactly what FA002 and FA004 need. GOTCHA (gate G2.9): from 3.14 on, get_children()
        also returns `__annotate__` tables (PEP 649), so we keep only symtable.Function ones.

    Args:
        info: the module info to fill in.
    """
    try:
        top = symtable.symtable(info.source, str(info.path), "exec")
    except SyntaxError:
        return
    for symbol in top.get_symbols():
        if symbol.is_assigned() or symbol.is_imported():
            info.module_names.add(symbol.get_name())
    stack = [table for table in top.get_children() if isinstance(table, symtable.Function)]
    while stack:
        table = stack.pop()
        info.frees[table.get_name()] = table.get_frees()
        for symbol in table.get_symbols():
            if symbol.is_declared_global() and symbol.is_assigned():
                info.global_writers.setdefault(symbol.get_name(), set()).add(table.get_name())
        stack.extend(t for t in table.get_children() if isinstance(t, symtable.Function))


def _context_method(info: ModuleInfo, node: ast.AST) -> tuple[bool, str | None]:
    """Recognise `multiprocessing.get_context("x")` calls and variables bound to them.

    Why:
        `ctx = get_context('fork')` decides the start method of every process made from ctx, which
        changes severities.

    Args:
        info: the module info.
        node: an expression used as a call receiver.

    Returns:
        (is a context, its start method or None if unknown).
    """
    if isinstance(node, ast.Name) and node.id in info.ctx_vars:
        return True, info.ctx_vars[node.id]
    if isinstance(node, ast.Call) and info.resolve(node.func) == "multiprocessing.get_context":
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return True, node.args[0].value
        return True, None
    return False, None


def _collect_bindings(tree: ast.Module, info: ModuleInfo) -> None:
    """Find variables bound to contexts, pools and Process subclasses, and set_start_method("fork").

    Why:
        `ctx = mp.get_context("spawn"); ctx.Process(...)` and `pool = mp.Pool(); pool.map(f, xs)`
        only make sense once we know what `ctx` and `pool` are.

    Args:
        tree: the parsed module.
        info: the module info to fill in.
    """
    # WHY: classes first, so `d = Daemon(...)` below knows Daemon is a Process subclass.
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                is_ctx_process = isinstance(base, ast.Attribute) and base.attr == "Process"
                if info.resolve(base) in PROCESS_NAMES or is_ctx_process:
                    info.process_subclasses.add(node.name)
    for node in ast.walk(tree):
        is_simple = isinstance(node, ast.Assign) and len(node.targets) == 1
        if is_simple and isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            is_ctx, method = _context_method(info, node.value)
            if is_ctx:
                info.ctx_vars[name] = method
            kind = _constructor_kind(info, node.value)
            if kind in ("pool", "executor") and isinstance(node.value, ast.Call):
                info.pool_vars[name] = kind
                info.pool_ctors[name] = node.value
            if kind == "subclass" and isinstance(node.value, ast.Call):
                info.subclass_vars[name] = node.value
        elif isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                expr = item.context_expr
                kind = _constructor_kind(info, expr)
                as_name = item.optional_vars
                # `with mp.Pool(2) as pool:` binds `pool` to a Pool, just like `pool = mp.Pool(2)`.
                if (
                    kind in ("pool", "executor")
                    and isinstance(as_name, ast.Name)
                    and isinstance(expr, ast.Call)
                ):
                    info.pool_vars[as_name.id] = kind
                    info.pool_ctors[as_name.id] = expr
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and id(node) in info.toplevel_ids):
            continue
        selects_fork = is_fork_selection(info, node)
        if selects_fork and info.resolve(node.func) == "multiprocessing.set_start_method":
            line = node.lineno
            info.fork_set_line = (
                line if info.fork_set_line is None else min(info.fork_set_line, line)
            )


def is_fork_selection(info: ModuleInfo, call: ast.Call) -> bool:
    """Whether a call is set_start_method("fork") or get_context("fork").

    Args:
        info: the module info.
        call: a call node.

    Returns:
        True when it explicitly selects the fork start method.
    """
    name = info.resolve(call.func)
    if name not in ("multiprocessing.set_start_method", "multiprocessing.get_context"):
        return False
    values = list(call.args[:1]) + [kw.value for kw in call.keywords if kw.arg == "method"]
    return any(isinstance(v, ast.Constant) and v.value == "fork" for v in values)


def _constructor_kind(info: ModuleInfo, node: ast.AST) -> str | None:
    """Classify an expression that creates a process, pool, executor or manager.

    Why:
        Process, Pool, Executor and Manager create processes in different ways and send different
        things to the child.

    Args:
        info: the module info.
        node: an expression.

    Returns:
        "process", "pool", "executor", "manager", "subclass" or None.
    """
    if not isinstance(node, ast.Call):
        return None
    name = info.resolve(node.func)
    if isinstance(node.func, ast.Attribute):
        is_ctx, _ = _context_method(info, node.func.value)
        if is_ctx:
            name = f"multiprocessing.{node.func.attr}"
    if name in PROCESS_NAMES:
        return "process"
    if name in POOL_NAMES:
        return "pool"
    if name in EXECUTOR_NAMES:
        return "executor"
    if name in MANAGER_NAMES:
        return "manager"
    if isinstance(node.func, ast.Name) and node.func.id in info.process_subclasses:
        return "subclass"
    return None


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    """Return a call's keyword argument value, or None.

    Args:
        call: the call.
        name: the keyword.

    Returns:
        The value expression or None.
    """
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _process_child_args(call: ast.Call) -> tuple[ast.expr, ...]:
    """The values a Process sends to its child: elements of args=(...) and kwargs={...} values.

    Why:
        Everything in args= and kwargs= is pickled under spawn and forkserver; FA007 checks each
        value.

    Args:
        call: a Process(...) call.

    Returns:
        The argument expressions.
    """
    values: list[ast.expr] = []
    args = _keyword(call, "args")
    if args is None and len(call.args) >= 4:
        args = call.args[3]
    if isinstance(args, ast.Tuple | ast.List):
        values.extend(args.elts)
    kwargs = _keyword(call, "kwargs")
    if isinstance(kwargs, ast.Dict):
        values.extend(v for v in kwargs.values if v is not None)
    return tuple(values)


def _site_for_call(
    info: ModuleInfo, call: ast.Call, custom_apis: frozenset[str]
) -> ProcSite | None:
    """Build a ProcSite if this call creates a process or submits work to a pool.

    Why:
        The table of K2 spec §5.6 step 4, written as code.

    Args:
        info: the module info.
        call: any call node.
        custom_apis: user-declared wrappers (--process-api).

    Returns:
        A ProcSite or None.
    """
    func = info.enclosing.get(id(call))
    method: str | None = None
    if isinstance(call.func, ast.Attribute):
        _is_ctx, method = _context_method(info, call.func.value)
    kind = _constructor_kind(info, call)
    if kind == "process":
        target = _keyword(call, "target")
        if target is None and len(call.args) >= 2:
            target = call.args[1]
        return ProcSite(call, "process", func, method, target, _process_child_args(call), None)
    if kind in ("pool", "executor", "manager"):
        initializer = _keyword(call, "initializer")
        return ProcSite(call, kind, func, method, None, (), initializer)
    if kind == "subclass":
        return None  # WHY: a subclass is reported at its .start() call (below)
    if isinstance(call.func, ast.Attribute) and call.func.attr == "start":
        # WHY: a Process subclass's process starts at `.start()`, which is where we report it.
        start_receiver = call.func.value
        creation: ast.Call | None = None
        if isinstance(start_receiver, ast.Name):
            creation = info.subclass_vars.get(start_receiver.id)
        elif (
            isinstance(start_receiver, ast.Call)
            and _constructor_kind(info, start_receiver) == "subclass"
        ):
            creation = start_receiver
        if creation is not None:
            sent = _process_child_args(creation) + tuple(creation.args)
            return ProcSite(call, "subclass", func, None, creation.func, sent, None)
    if isinstance(call.func, ast.Attribute):
        receiver = call.func.value
        receiver_kind = None
        receiver_method: str | None = None
        if isinstance(receiver, ast.Name) and receiver.id in info.pool_vars:
            receiver_kind = info.pool_vars[receiver.id]
        else:
            receiver_kind = _constructor_kind(info, receiver)
            if isinstance(receiver, ast.Call) and isinstance(receiver.func, ast.Attribute):
                _, receiver_method = _context_method(info, receiver.func.value)
        methods = POOL_METHODS if receiver_kind == "pool" else EXECUTOR_METHODS
        if receiver_kind in ("pool", "executor") and call.func.attr in methods and call.args:
            kind_name = "pool_call" if receiver_kind == "pool" else "executor_call"
            return ProcSite(
                call, kind_name, func, receiver_method, call.args[0], tuple(call.args[1:]), None
            )
    name = info.resolve(call.func)
    if name is not None and name in custom_apis:
        return ProcSite(call, "custom", func, None, None, (), None)
    return None


def build_module(
    path: Path, display: str, source: str, custom_apis: frozenset[str] = frozenset()
) -> ModuleInfo:
    """Parse one file and collect everything the rules need.

    Why:
        The single entry point the scanner calls per file.

    Args:
        path: the file's absolute path.
        display: the path as shown in reports.
        source: the file's text.
        custom_apis: user-declared process-creating wrappers.

    Returns:
        The ModuleInfo.

    Raises:
        SyntaxError: the file doesn't parse (the scanner turns this into FA000).
    """
    # CONCEPT(ast-and-symtable): parse the text into a tree of nodes; nothing is executed.
    tree = ast.parse(source, filename=str(path), type_comments=False)
    info = ModuleInfo(
        path=path, display=display, source=source, tree=tree, lines=source.splitlines()
    )
    _collect_aliases(tree, info)
    _collect_toplevel(tree.body, info)
    _collect_scopes(tree, info)
    _collect_symtable(info)
    _collect_bindings(tree, info)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            site = _site_for_call(info, node, custom_apis)
            if site is not None:
                info.sites.append(site)
    info.sites.sort(key=_site_line)
    return info


def _site_line(site: ProcSite) -> int:
    """Sort key: a site's line.

    Args:
        site: the site.

    Returns:
        Its line.
    """
    return site.line


def anchor_for(info: ModuleInfo, site: ProcSite) -> ast.Call:
    """The call a finding about this site is reported on: the process-creation line.

    Why:
        One convention for every rule (and for the corpus's expected.json): the `Process(...)`
        call, the `.start()` of a Process subclass, or the `Pool(...)`/`ProcessPoolExecutor(...)`
        that a `pool.map(...)` uses.

    Args:
        info: the module info.
        site: the site.

    Returns:
        The call node to report on.
    """
    if site.kind not in ("pool_call", "executor_call") or not isinstance(
        site.call.func, ast.Attribute
    ):
        return site.call
    receiver = site.call.func.value
    if isinstance(receiver, ast.Call):
        return receiver
    if isinstance(receiver, ast.Name) and receiver.id in info.pool_ctors:
        return info.pool_ctors[receiver.id]
    return site.call
