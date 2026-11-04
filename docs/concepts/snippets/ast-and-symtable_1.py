"""Source text -> syntax tree (ast) -> scopes (symtable)."""

import ast
import symtable

SOURCE = """
import multiprocessing as mp

def test_lock():
    count = 0
    def target():
        print(count)
    mp.Process(target=target).start()
"""

tree = ast.parse(SOURCE)
for node in ast.walk(tree):
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Process"
    ):
        keyword = node.keywords[0]
        print(
            f"line {node.lineno}: a call to .Process with {keyword.arg}={ast.unparse(keyword.value)}"
        )

table = symtable.symtable(SOURCE, "<demo>", "exec")
outer = next(child for child in table.get_children() if child.get_name() == "test_lock")
inner = next(child for child in outer.get_children() if child.get_name() == "target")
print("scopes:", table.get_name(), "->", outer.get_name(), "->", inner.get_name())
print(
    "is 'target' nested inside another function?",
    inner.get_type() == "function" and outer.get_type() == "function",
)
print("free variables of target (read from the enclosing function):", inner.get_frees())
