# symtable (which names live in which scope)

> **First used in:** see the [ledger](README.md) (milestone K2) · **Needs:** [ast-module](ast-module.md), [closures](closures.md) · **Reading time:** ~6 min
> **Terms introduced:** **`symtable.symtable`**, **SymbolTable**, **Symbol**, **`__annotate__` scope**

## What it looks like

```python
import symtable
top = symtable.symtable(source_text, "<file>", "exec")   # the module's table
for child in top.get_children():                          # nested scopes: functions, classes...
    print(child.get_name(), child.get_type())
```

## What it means

The compiler decides, for every name in every function, *where it lives*:
- **local:** assigned in this function;
- **global:** the module's variable, explicitly with `global`;
- **free:** read from an enclosing function, which makes it a closure.

`symtable` exposes the compiler's answers.

- `symtable.symtable(code, filename, "exec")` returns the module's **SymbolTable**.
- `table.get_children()` returns the tables of the scopes nested directly inside it. `get_name()` and `get_type()` (`function`, `class`, `module`...) describe each one.
- `table.lookup("name")` returns a **Symbol** with questions like `is_local()`, `is_global()`, `is_declared_global()`, `is_free()` and `is_parameter()`.
- `function_table.get_frees()` lists the free variables of a function.

## Why people use it

When a tool needs to know *what a name refers to*, not only that it appears. "Is this function defined inside another function?" and "does this function assign a module-level global?" have tricky answers (`global`, `nonlocal`, class bodies, comprehensions), and `symtable` gets them right because it *is* the compiler's analysis.

## How Kiln uses it

[`src/kiln/forkcheck/symbols.py`](../../src/kiln/forkcheck/symbols.py) records each function's symtable facts:
- FA002 uses "nested function?" (unpicklable target);
- FA004 uses `is_declared_global()` (a global rebound at run time, which a spawned child won't see).
Both rules live in [`src/kiln/forkcheck/rules.py`](../../src/kiln/forkcheck/rules.py).

## Gotchas

- **Look scopes up by name, not position.** On Python 3.14+, annotations are evaluated lazily (PEP 649), and the module gets an extra scope named **`__annotate__`**. On 3.15 it comes *first* in `get_children()` even here; on 3.13 the same code gives only `['outer']`. Code that used `get_children()[0]` broke on this.
- **`get_type()` returns an enum on 3.13+.** Compare with `str(...)` or the `SymbolTableType` members.
- **It analyses source text you give it,** not live objects, and doesn't follow imports.

## Try it

```text
>>> import symtable
>>> code = "LIMIT = 5\ndef outer():\n    seen = []\n    def inner(x):\n        global LIMIT\n        seen.append(x)\n        return x < LIMIT\n    return inner\n"
>>> top = symtable.symtable(code, "<demo>", "exec")
>>> [child.get_name() for child in top.get_children()]
['__annotate__', 'outer']
>>> outer = next(c for c in top.get_children() if c.get_name() == "outer")
>>> inner = next(c for c in outer.get_children() if c.get_name() == "inner")
>>> str(inner.get_type()), inner.get_name()
('function', 'inner')
>>> inner.get_frees()
('seen',)
>>> inner.lookup("LIMIT").is_declared_global()
True
>>> inner.lookup("seen").is_free()
True
>>> inner.lookup("x").is_parameter()
True
>>> outer.lookup("seen").is_local()
True
```

(Generated from [`snippets/symtable-module.repl`](snippets/symtable-module.repl) on Python 3.15.1. On 3.13.17 the fourth line prints `['outer']`.)

`inner` borrows `seen` from `outer` (free in `inner`, local in `outer`), declares `LIMIT` global, and has one parameter `x`. That's exactly the information forkcheck needs and `ast` alone can't give.

## Check yourself

1. In the example, is `seen` local to `inner`?
   <details><summary>Answer</summary>

   No. It's *free* in `inner`, borrowed from `outer`, where it's local. `inner.lookup("seen").is_free()` is True.

   </details>
2. Why did the first version of this lesson's code crash with `IndexError` on Python 3.15?
   <details><summary>Answer</summary>

   It took `top.get_children()[0]`, assuming that was `outer`. On 3.15 the first child is the `__annotate__` scope (PEP 649), so the "outer" it picked had no children. Look scopes up by name.

   </details>
3. Which method tells you a function rebinds a module-level variable with `global`?
   <details><summary>Answer</summary>

   `function_table.lookup(name).is_declared_global()`. forkcheck's FA004 uses it to spot globals changed at run time.

   </details>
