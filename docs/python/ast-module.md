# ast (Python code as a tree)

> **First used in:** see the [ledger](README.md) (milestone K2) · **Needs:** [classes-and-objects](classes-and-objects.md) · **Reading time:** ~8 min
> **Terms introduced:** **`ast.parse`**, **`ast.dump`**, **`ast.walk`**, **`ast.NodeVisitor`**, **`ast.unparse`**

## What it looks like

```python
import ast
tree = ast.parse("total = price * 2")          # source text → tree
print(ast.dump(tree, indent=2))                 # show the tree
for node in ast.walk(tree):                     # visit every node
    if isinstance(node, ast.Name):
        print(node.id, node.lineno)
```

## What it means

- **`ast.parse(source)`** runs Python's real parser and returns a tree of **node** objects, such as `Module`, `Assign`, `Name`, `Call`, `Lambda` or `Constant`. Each node has named fields (an `Assign` has `targets` and `value`) and a position (`lineno`, `col_offset`, `end_lineno`).
- **`ast.dump(tree, indent=2)`** prints the tree, which is the easiest way to learn what a piece of code looks like to Python.
- **`ast.walk(tree)`** yields every node in the tree, in no particular order.
- **`ast.NodeVisitor`** is a class you subclass, writing `visit_Call`, `visit_Name`... methods. `visitor.visit(tree)` calls the method matching each node's type. Call `self.generic_visit(node)` to keep going into a node's children.
- **`ast.unparse(node)`** turns a node back into source text, which is handy in messages.

## Why people use it

Any tool that *reads* code uses it: linters, formatters, refactoring tools, test runners that rewrite `assert`. It's far more reliable than searching text. It doesn't confuse code with strings or comments, and it gives exact positions.

## How Kiln uses it

- [`scripts/check_docs.py`](../../scripts/check_docs.py) (K0) walks the AST of every code file to build the Python ledger.
- [`src/kiln/forkcheck/scanner.py`](../../src/kiln/forkcheck/scanner.py) and [`src/kiln/forkcheck/symbols.py`](../../src/kiln/forkcheck/symbols.py) (K2) parse every file, find process-creation `Call` nodes, guard regions (`If` nodes testing `__name__`) and imports.
- [`src/kiln/forkcheck/rules.py`](../../src/kiln/forkcheck/rules.py) checks the shapes of target expressions (`Lambda`, `Name`, `Call` to `partial`).

## Gotchas

- **Only `ast.Constant` for literals.** `ast.Num` and `ast.Str` were removed in recent Pythons; numbers and strings are all `Constant` (K2 gate G2.10).
- **`ast.walk` order isn't source order.** Sort by `lineno` if order matters.
- **The tree is syntax, not meaning.** `Name(id='target')` says a name is used, not what it refers to. Use `symtable` for scopes ([symtable-module](symtable-module.md)).
- **A syntax error raises `SyntaxError`** from `parse`. Catch it if you scan many files.

## Try it

```text
>>> import ast
>>> tree = ast.parse("total = price * 2")
>>> print(ast.dump(tree, indent=2))
Module(
  body=[
    Assign(
      targets=[
        Name(id='total', ctx=Store())],
      value=BinOp(
        left=Name(id='price'),
        op=Mult(),
        right=Constant(value=2)))])
>>> node = tree.body[0]
>>> type(node).__name__, node.lineno, node.col_offset
('Assign', 1, 0)
>>> ast.unparse(node.value)
'price * 2'
>>> class CallFinder(ast.NodeVisitor):
...     def visit_Call(self, node):
...         print("call on line", node.lineno, "->", ast.unparse(node.func))
...         self.generic_visit(node)
... 
>>> CallFinder().visit(ast.parse("print(len(items))\nmp.Process(target=work).start()"))
call on line 1 -> print
call on line 1 -> len
call on line 2 -> mp.Process(target=work).start
call on line 2 -> mp.Process
>>> ast.parse("x = = 1")
Traceback (most recent call last):
  File "<console>", line 1, in <module>
  File "/home/Megatron/.local/share/uv/python/cpython-3.15.1-linux-x86_64-gnu/lib/python3.15/ast.py", line 46, in parse
    return compile(source, filename, mode, flags,
                   _feature_version=feature_version, optimize=optimize,
                   module=module)
  File "<unknown>", line 1
    x = = 1
        ^
SyntaxError: invalid syntax
```

(Generated from [`snippets/ast-module.repl`](snippets/ast-module.repl) on Python 3.15.1.)

Notice `ctx=Store()` on `total` (being assigned) and no `ctx` shown for `price`, because the default, `Load()`, is hidden in 3.15's dump. `mp.Process(target=work).start()` is *two* calls: the outer `.start()` and the inner `mp.Process(...)`, which is why the visitor reports line 2 twice.

## Check yourself

1. What node type represents `lambda: 1`?
   <details><summary>Answer</summary>

   `ast.Lambda`, with `args` and a `body` (here `Constant(value=1)`). forkcheck's FA002 looks for it as a process target.

   </details>
2. Why does `visit_Call` call `self.generic_visit(node)`?
   <details><summary>Answer</summary>

   To continue into the call's children. Without it, calls nested inside a call's arguments (like `len(...)` inside `print(...)`) wouldn't be visited.

   </details>
3. Predict what `ast.unparse(ast.parse("a=1+2").body[0])` returns.
   <details><summary>Answer</summary>

   `'a = 1 + 2'`. `unparse` writes normalised source, with its own spacing.

   </details>
