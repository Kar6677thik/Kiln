"""A deliberately naive checker: flag `Process(target=lambda ...)` by reading code, not running it.

It finds the real bug, misses one written differently, and wrongly flags one that is fine.
"""

import ast

CASES = {
    "a: lambda target (real bug)": "mp.Process(target=lambda: 1).start()",
    "b: lambda via a variable (real bug)": "f = lambda: 1\nmp.Process(target=f).start()",
    "c: lambda under fork context (works)": "mp.get_context('fork').Process(target=lambda: 1).start()",
}


def naive_check(source):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "Process":
            for keyword in node.keywords:
                if keyword.arg == "target" and isinstance(keyword.value, ast.Lambda):
                    return True
    return False


for name, source in CASES.items():
    print(f"{name:<38} flagged: {naive_check(source)}")
