"""Tests for kiln.forkcheck.symbols: what the checker learns from one module's source.

These pin down the facts every rule builds on: guard regions, top-level statements, import
aliases, nested functions (via symtable, gate G2.9), and process-creation sites (§5.6).
"""

import ast
import symtable
import textwrap
from pathlib import Path

import pytest

from kiln.forkcheck.symbols import ModuleInfo, anchor_for, build_module, is_main_guard

PACKAGE = Path(__file__).resolve().parents[3] / "src" / "kiln" / "forkcheck"


def module(source: str, apis: frozenset[str] = frozenset()) -> ModuleInfo:
    """Build a ModuleInfo from an indented snippet."""
    return build_module(Path("/virtual/m.py"), "m.py", textwrap.dedent(source), apis)


def guard_test(expression: str) -> ast.expr:
    """Parse `expression` and return it as an expression node."""
    statement = ast.parse(expression).body[0]
    assert isinstance(statement, ast.Expr)
    return statement.value


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ('__name__ == "__main__"', True),
        ('"__main__" == __name__', True),
        ('__name__ != "__main__"', False),
        ('__name__ == "main"', False),
        ('name == "__main__"', False),
    ],
)
def test_main_guard_accepts_both_orders_and_only_equality(expression: str, expected: bool) -> None:
    """The guard is `__name__ == "__main__"` in either order, with `==` only (§5.6 step 3)."""
    assert is_main_guard(guard_test(expression)) is expected


def test_guard_ranges_cover_the_guard_body() -> None:
    """Lines inside the guard body are in a guard region; lines before it are not."""
    info = module(
        """
        import os
        if __name__ == "__main__":
            os.getpid()
            os.getppid()
        """
    )
    assert info.in_guard(4)
    assert info.in_guard(5)
    assert not info.in_guard(2)


def test_toplevel_recurses_into_if_and_try_but_not_into_def() -> None:
    """Calls in top-level if/try are top level; calls inside a def or the guard are not."""
    info = module(
        """
        import os
        if True:
            try:
                os.getpid()
            finally:
                pass
        def f():
            os.getppid()
        if __name__ == "__main__":
            os.getcwd()
        """
    )
    calls = {
        ast.unparse(node.func): id(node) in info.toplevel_ids
        for node in ast.walk(info.tree)
        if isinstance(node, ast.Call)
    }
    assert calls == {"os.getpid": True, "os.getppid": False, "os.getcwd": False}


@pytest.mark.parametrize(
    "source",
    [
        "import multiprocessing as mp\nmp.Process(target=print)\n",
        "from multiprocessing import Process as P\nP(target=print)\n",
        "import multiprocessing\nmultiprocessing.Process(target=print)\n",
    ],
    ids=["module-alias", "name-alias", "plain"],
)
def test_aliases_resolve_to_multiprocessing_process(source: str) -> None:
    """However Process is imported, the call is recognised as a process site."""
    info = module(source)
    assert [site.kind for site in info.sites] == ["process"]


def test_nested_function_and_its_free_variables_come_from_symtable() -> None:
    """A function defined inside another is recorded as nested, with what it captures (G2.9)."""
    info = module(
        """
        def outer():
            secret = 1
            def inner():
                return secret
            return inner
        """
    )
    outer = info.defs["outer"]
    assert info.nested_funcs[id(outer)] == {"inner"}
    assert info.frees["inner"] == ("secret",)


def test_symtable_children_include_annotate_scopes_on_new_pythons() -> None:
    """G2.9: on 3.14+ get_children() can also return `__annotate__` tables (not Functions).

    The checker filters on isinstance(..., symtable.Function), so it still works there.
    """
    source = "def f(x: int) -> int:\n    def g() -> None:\n        return None\n    return x\n"
    table = symtable.symtable(source, "m.py", "exec")
    functions = [t for t in table.get_children() if isinstance(t, symtable.Function)]
    assert [t.get_name() for t in functions] == ["f"]
    assert functions[0].get_frees() == ()
    info = module(source)
    assert info.nested_funcs[id(info.defs["f"])] == {"g"}


def test_global_declaration_is_seen() -> None:
    """`global G` inside a function is recorded as that function writing G (G2.9)."""
    info = module(
        """
        G = 1
        def set_g():
            global G
            G = 2
        """
    )
    assert "set_g" in info.global_writers["G"]


@pytest.mark.parametrize(
    ("source", "kinds"),
    [
        (
            "import multiprocessing as mp\nwith mp.Pool(2) as p:\n    p.map(abs, [1])\n",
            ["pool", "pool_call"],
        ),
        (
            "from concurrent.futures import ProcessPoolExecutor as E\nx = E()\nx.submit(abs, 1)\n",
            ["executor", "executor_call"],
        ),
        ("import multiprocessing as mp\nm = mp.Manager()\n", ["manager"]),
        (
            "import multiprocessing as mp\nctx = mp.get_context('spawn')\nctx.Process(target=abs)\n",
            ["process"],
        ),
    ],
    ids=["pool", "executor", "manager", "context"],
)
def test_site_kinds(source: str, kinds: list[str]) -> None:
    """Each API of the §5.6 table produces a site of the right kind."""
    assert [site.kind for site in module(source).sites] == kinds


def test_context_receiver_records_the_method() -> None:
    """A Process made from get_context("fork") remembers that it uses fork."""
    info = module(
        "import multiprocessing as mp\nctx = mp.get_context('fork')\nctx.Process(target=abs)\n"
    )
    assert [site.method for site in info.sites] == ["fork"]


def test_subclass_site_is_the_start_call() -> None:
    """For a Process subclass, the site is `.start()` and the target is the class (§5.6)."""
    info = module(
        """
        import multiprocessing as mp
        class D(mp.Process):
            def run(self):
                pass
        d = D()
        d.start()
        """
    )
    (site,) = info.sites
    assert site.kind == "subclass"
    assert site.line == 7


def test_pool_work_is_anchored_on_the_pool_creation() -> None:
    """pool.map(...) findings point at the Pool(...) line (the corpus anchor convention)."""
    info = module(
        """
        import multiprocessing as mp
        with mp.Pool(2) as pool:
            pool.map(abs, [1])
        """
    )
    work = next(site for site in info.sites if site.kind == "pool_call")
    assert anchor_for(info, work).lineno == 3


def test_process_api_option_declares_custom_sites() -> None:
    """--process-api names a user wrapper that counts as process creation."""
    info = module("import mylib\nmylib.spawn_worker()\n", frozenset({"mylib.spawn_worker"}))
    assert [site.kind for site in info.sites] == ["custom"]


def test_syntax_errors_propagate() -> None:
    """build_module raises SyntaxError; the scanner turns it into FA000."""
    with pytest.raises(SyntaxError):
        module("def broken(:\n")


def test_only_ast_constant_is_used() -> None:
    """G2.10: the removed classes ast.Num/ast.Str/ast.Bytes/ast.NameConstant are never used."""
    for path in PACKAGE.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for removed in ("ast.Num", "ast.Str", "ast.Bytes", "ast.NameConstant", "ast.Ellipsis"):
            assert removed not in text, f"{path.name} uses {removed}"
