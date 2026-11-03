# Decorators

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [functions](functions.md), [classes and objects](classes-and-objects.md) · **Reading time:** ~12 min
> **Terms introduced:** **decorator**, **first-class function**, **wrapper function**, **decorator factory**, **functools.wraps**

## What it looks like

```python
def shout(func):  # a decorator: takes a function...
    def wrapper(name):
        return func(name).upper()

    return wrapper  # ...and returns a (new) function


@shout  # the @ line "decorates" the def below it
def welcome(name):
    return "welcome " + name


welcome("ada")  # 'WELCOME ADA'
```

## What it means

Start with one fact that surprises many newcomers. In Python, a **function is a value**, the same kind of thing as a number or a string. You can store it in a variable, put it in a list or a dict, pass it to another function, and return it from a function. That property is called being a **first-class function**.

A **decorator** is an ordinary function that takes a function as input and returns a function as output. Nothing more.

Here is the example above, line by line:

1. `def shout(func):` defines a function whose parameter `func` will hold *another function*.
2. Inside it, `def wrapper(name):` defines a new function on the spot. This is the **wrapper function**: it will run *instead of* the original one.
3. `return func(name).upper()` means: when the wrapper runs, it calls the original function, then changes its result (makes it uppercase).
4. `return wrapper` hands the new function back to whoever called `shout`.
5. `@shout` placed above `def welcome(...)` tells Python: "right after you create `welcome`, pass it through `shout`, and keep what comes back under the name `welcome`."

So the `@` line is shorthand. These two pieces of code do exactly the same thing:

```python
@shout
def welcome(name):
    return "welcome " + name
```

```python
def welcome(name):
    return "welcome " + name


welcome = shout(welcome)
```

After this, the name `welcome` points at `wrapper`, and `wrapper` still remembers the original `welcome` in its `func` variable. A nested function remembering a variable from the function around it is called a *closure*. You don't need the details yet; K2 has a note on it.

### Decorators with arguments

Sometimes you want options: `@app.task(timeout_s=120)`. Now `app.task(timeout_s=120)` is *called first*, and whatever it returns is the real decorator. A function that builds and returns a decorator is called a **decorator factory**. It has three layers:

```python
def task(name=None):  # layer 1: receives the options
    def decorator(func):  # layer 2: the real decorator, receives the function
        TASKS[name or func.__name__] = func
        return func  # registers the function and returns it unchanged

    return decorator


@task(name="img.resize")  # same as: resize = task(name="img.resize")(resize)
def resize(path, width): ...
```

Notice that this decorator doesn't wrap anything. It **registers** the function in a table and returns it unchanged. That's a very common use, and it's exactly what Kiln's `@app.task` does (spec/03 §2: "The decorator returns the original function, unchanged").

### `functools.wraps`

A wrapper replaces the original function, so it also replaces its name and docstring. In the "Try it" session below, `welcome.__name__` prints `'wrapper'`, which is confusing in error messages and tests. **`functools.wraps`** is a decorator from the standard library. You put it on your wrapper, and it copies the original function's name, docstring and a few other details onto the wrapper:

```python
import functools


def logged(func):
    @functools.wraps(func)  # copy func's __name__ and __doc__ onto wrapper
    def wrapper(*args, **kwargs):  # *args/**kwargs: accept any arguments (see functions.md)
        print(f"calling {func.__name__} with {args}")
        return func(*args, **kwargs)

    return wrapper
```

## Why people use it

Decorators let you **add behaviour to many functions without editing each one**. The decorator lives in one place, and you opt in with a single `@` line. Common uses:

- **Registration:** collecting functions into a table (`@app.task`, web frameworks' `@app.route("/path")`).
- **Wrapping:** timing, logging, retrying or caching a call (`@functools.lru_cache` arrives in K6).
- **Code generation:** `@dataclass` reads a class and writes `__init__`, `__repr__` and `__eq__` for you (see [dataclasses](dataclasses.md)).

Without decorators you would write `resize = task(name="img.resize")(resize)` *after* every function. It's easy to forget, and the important information ends up far below the `def` line instead of right above it.

## How Kiln uses it

- [`src/kiln/doctor.py`](../../src/kiln/doctor.py) uses `@dataclass(frozen=True)` (a decorator *with* an argument) to generate the `CheckResult` class.
- [`scripts/check_docs.py`](../../scripts/check_docs.py) uses `@dataclass` for its `Problem` records.
- Tests in [`tests/unit/`](../../tests/unit/) use `@pytest.mark.parametrize(...)` to run one test function with many inputs (see [pytest basics](pytest-basics.md)).
- From K1, `@app.task` registers job functions (spec/03 §2). It's a decorator factory exactly like `task(...)` above.

## Gotchas

- **Forgetting `return wrapper`.** A decorator that returns nothing replaces your function with `None`:
  ```python
  def broken(func):
      def wrapper():
          return func()

      # oops: no "return wrapper"


  @broken
  def hello():
      return "hi"


  hello()  # TypeError: 'NoneType' object is not callable
  ```
- **`@task` vs `@task()`.** With a decorator factory, the parentheses matter. `@task` passes your function in as the `name` option, and things go wrong in confusing ways. Kiln's `@app.task` accepts *both* spellings on purpose (spec/03 §2), and K1's note explains how.
- **Lost names.** Without `functools.wraps`, every wrapped function reports itself as `wrapper`.
- **Order of stacked decorators.** With `@a` on top of `@b` on top of `def f`, the bottom decorator (`b`) is applied first: `f = a(b(f))`.
- **Decorators run once, at definition time.** The decorator itself runs when Python reaches the `def` (usually at import). Only the *wrapper* runs on each call. That's why registration with `@app.task` happens the moment the module is imported.

## Try it

```text
>>> def shout(func):
...     def wrapper(name):
...         return func(name).upper()
...     return wrapper
... 
>>> def greet(name):
...     return "hello " + name
... 
>>> greet("ada")
'hello ada'
>>> loud = shout(greet)
>>> loud("ada")
'HELLO ADA'
>>> @shout
... def welcome(name):
...     return "welcome " + name
... 
>>> welcome("ada")
'WELCOME ADA'
>>> welcome.__name__
'wrapper'
```

The last line shows the "lost name" gotcha. A longer example with a decorator factory and `functools.wraps` is in [`snippets/decorators_1.py`](snippets/decorators_1.py):

```text
(wsl) $ python3.15 docs/python/snippets/decorators_1.py
registered: ['add', 'img.resize']
add(2, 3) = 5
lookup by name: resized cat.png to 100px
calling double with (4,)
double returned 8
name: double | doc: Return x times two.
```

Thanks to `functools.wraps`, `double.__name__` is `double`, not `wrapper`.

## Check yourself

1. Rewrite `@shout` above `def welcome(...)` without using the `@` symbol.
   <details><summary>Answer</summary>

   Define `welcome` normally, then write `welcome = shout(welcome)` on the next line. The `@` syntax is shorthand for exactly that reassignment.

   </details>
2. A decorator receives what, and returns what?
   <details><summary>Answer</summary>

   It receives a function (or a class) and returns a function (or class) that takes its place. That can be a new wrapper, or the same object after registering it somewhere.

   </details>
3. In `@task(name="x")`, how many function calls happen when Python reaches the `def` below it?
   <details><summary>Answer</summary>

   Two. First `task(name="x")` runs and returns the real decorator. Then that decorator is called with the newly created function. The function's *own* body doesn't run until someone calls it later.

   </details>
4. Why does Kiln's `@app.task` return the original function unchanged, instead of a wrapper?
   <details><summary>Answer</summary>

   Its only job is registration: putting the function in the app's task table. Returning the original means you can still call `add(2, 3)` directly in a test, with no hidden extra behaviour (spec/03 §2).

   </details>
