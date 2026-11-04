# Reading Code Without Running It: `ast` and `symtable`

> **Milestone:** K2 · **Read before:** [ast-module](../python/ast-module.md), [closures](../python/closures.md) · **Reading time:** ~13 min
> **Terms introduced:** **abstract syntax tree (AST)**, **node**, **scope**, **free variable**, **symbol table**

## One-sentence summary

Python can turn source code into an **abstract syntax tree** (`ast`): a tree of **nodes** like "Call", "Name" and "Lambda". It can also compute a **symbol table** (`symtable`) that says, for every name in every **scope**, whether it is local, global or a **free variable** borrowed from an enclosing function. Together they let a tool answer questions about code without running it.

## Why you should care

`kiln forkcheck` is built on these two modules. To report "`target` is a nested function passed to `Process`" (FA002), it must
1. find the call `mp.Process(target=target)` in the tree, and
2. know that the name `target` refers to a function defined *inside another function*.

The tree gives (1). Only the symbol table reliably gives (2), because Python's scoping rules (local, enclosing, global, `nonlocal`, comprehensions, class bodies) are subtle, and `symtable` uses the compiler's own answer.

## The mental model

**Analogy:** a sentence diagram from grammar class. `ast` is the diagram: subject, verb, object, nested clauses. `symtable` is the footnotes saying which "it" refers to which noun.

**Where the analogy fails:** a diagram tells you what the sentence *says*, not what *happens*. `ast` can't know what a variable holds at run time, which functions are called, or whether an `if` branch is taken. Static analysis always works with those blind spots ([static-vs-dynamic-analysis](static-vs-dynamic-analysis.md)).

```
 source:  def test_lock():                 ast (tree)                         symtable (scopes)
              count = 0                     Module                             top  (module)
              def target():                  └ FunctionDef test_lock             └ test_lock (function)
                  print(count)                   ├ Assign count = 0                    locals: count, target
              mp.Process(target=target)          ├ FunctionDef target                  └ target (function)
                  .start()                       └ Expr Call .start                        frees: count  ← borrowed
                                                      └ Call .Process(target=Name target)
```

## See it for yourself

[`snippets/ast-and-symtable_1.py`](snippets/ast-and-symtable_1.py) parses a miniature oslo-style test and asks both modules about it:

```bash
(wsl) $ ./scripts/dev.sh run docs/concepts/snippets/ast-and-symtable_1.py
```

```text
line 8: a call to .Process with target=target
scopes: top -> test_lock -> target
is 'target' nested inside another function? True
free variables of target (read from the enclosing function): ('count',)
```

- **Line 1** comes from `ast.walk`: it found the `Call` node whose function is an attribute named `Process`, and printed the keyword argument with `ast.unparse`.
- **Lines 2–4** come from `symtable`: three nested scopes, `target` is a function inside a function, and it reads `count` from its enclosing scope. That captured variable is exactly why pickle can't rebuild it by name.

## How it really works

- **Parsing.** `ast.parse(source)` runs CPython's real parser and returns a tree of node objects: `Module`, `FunctionDef`, `Call`, `Name`, `Lambda`, `Constant`... Each node has fields (`Call.func`, `Call.args`, `Call.keywords`) and positions (`lineno`, `col_offset`, `end_lineno`). Because it's the real parser, any file Python can run, the tree can represent, and a file with a `SyntaxError` can't be parsed. forkcheck then reports FA000.
- **Walking.** `ast.walk(tree)` visits every node. An `ast.NodeVisitor` subclass with `visit_Call(self, node)` methods visits by node type, and `generic_visit` continues into children.
- **Scopes.** `symtable.symtable(source, filename, "exec")` returns the module's table. `get_children()` gives nested scopes (functions, classes, comprehensions). On 3.14+ there are also `__annotate__` scopes for lazily evaluated annotations (PEP 649), which is why code should look scopes up by *name*, not position. Each `Symbol` answers `is_local()`, `is_global()`, `is_declared_global()` (a `global` statement), `is_free()` (from an enclosing function), `is_parameter()`...
- **The limits.** Neither module follows imports, evaluates expressions, or knows which branch runs. `target = make_target()` gives a `Name` with no hint of what `make_target` returns. forkcheck resolves names inside one module and one call level deep (K2 spec §5.6), and admits the rest as known false positives or negatives.

## How Kiln uses it

- [`src/kiln/forkcheck/scanner.py`](../../src/kiln/forkcheck/scanner.py): parses each file and finds process-creation sites (`ProcSite`).
- [`src/kiln/forkcheck/symbols.py`](../../src/kiln/forkcheck/symbols.py): builds the import alias table, guard regions and `symtable` facts per function.
- [`src/kiln/forkcheck/rules.py`](../../src/kiln/forkcheck/rules.py): rules FA001–FA008 combine both, for example "the target `Name` resolves to a function whose symtable says it's nested" → FA002.
- K0 already used `ast` for `scripts/check_docs.py` (the Python ledger's construct map).

## Common misconceptions

- ❌ **Myth:** "Static analysis can tell what a program will do."
  ✅ **Reality:** it can tell what the code *says*. Values, branches taken and dynamic imports are invisible. That's why forkcheck adds `--confirm`.
- ❌ **Myth:** "Searching the text for `lambda` is good enough."
  ✅ **Reality:** text search hits comments and strings, and misses the same thing spelled differently. The tree knows a `Lambda` node from the word "lambda" in a docstring.
- ❌ **Myth:** "I can work out scopes by looking at indentation."
  ✅ **Reality:** `global`, `nonlocal`, class bodies (which don't enclose methods) and comprehensions make that unreliable. `symtable` gives the compiler's actual answer.

## Check yourself

1. Which module tells you that `count` inside `target` comes from the enclosing function: `ast` or `symtable`?
   <details><summary>Answer</summary>

   `symtable`. It reports `count` as a *free* variable of `target`. `ast` only shows a `Name` node with `id='count'`, and leaves it to you to work out where it's defined.

   </details>
2. Predict: what does forkcheck do with a file containing `def f(:`?
   <details><summary>Answer</summary>

   `ast.parse` raises `SyntaxError`, so forkcheck reports FA000 ("could not parse", info) for that file and carries on scanning the others.

   </details>
3. Why can't `ast` alone know whether `mp.Process(target=build())` passes a nested function?
   <details><summary>Answer</summary>

   It sees a call to `build`, not its result. What `build()` returns is only known at run time, and that's a static-analysis blind spot.

   </details>

## Go deeper

- [Python docs: ast](https://docs.python.org/3.15/library/ast.html) and [symtable](https://docs.python.org/3.15/library/symtable.html).
- [Green Tree Snakes: the missing Python AST docs](https://greentreesnakes.readthedocs.io/).
- Python notes: [ast-module](../python/ast-module.md), [symtable-module](../python/symtable-module.md).
