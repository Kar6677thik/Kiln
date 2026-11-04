# pickle and `__reduce__`

> **First used in:** see the [ledger](README.md) (milestone K2) · **Needs:** [bytes-vs-str](bytes-vs-str.md), [classes-and-objects](classes-and-objects.md) · **Reading time:** ~8 min
> **Terms introduced:** **pickle**, **`pickle.dumps` / `pickle.loads`**, **pickle protocol**, **`__reduce__`**, **copyreg**

## What it looks like

```python
import pickle
data = pickle.dumps({"job": "add", "args": [2, 3]})   # object → bytes
again = pickle.loads(data)                              # bytes → an equal object
```

## What it means

- **`pickle.dumps(obj)`** turns almost any Python object into `bytes`. **`pickle.loads(data)`** rebuilds an equal object from them. Together they're "serialise and deserialise", Python-style.
- The bytes are a tiny program of opcodes for rebuilding the object. The **pickle protocol** is the version of that opcode language: higher protocols are more compact, and 5 is both the default and the highest on 3.15.
- **Functions and classes are stored by name** (module + qualified name), not as code. Unpickling imports the module and looks the name up. That's why nested functions and lambdas can't be pickled (see the [pickling](../concepts/pickling.md) primer).
- **`__reduce__`** is a method a class can define to say how to rebuild it: it returns `(callable, args)`, meaning "call this with these arguments". **`copyreg`** registers such reducers for classes you don't own.

## Why people use it

To send rich Python objects between processes (`multiprocessing` pickles targets, arguments and results under spawn and forkserver), and to cache objects on disk. It's convenient because it handles almost anything, including your own classes.

## How Kiln uses it

- Kiln **does not** use pickle for its own messages: KP/1 is JSON ([ADR-009](../decisions.md#adr-009)). `pickle.loads` on untrusted bytes can run arbitrary code, because a `__reduce__` can name any callable, such as `os.system`.
- Pickling appears as a *subject*. [`src/kiln/forkcheck/rules.py`](../../src/kiln/forkcheck/rules.py) FA002/FA007 predict pickling failures in code that uses `multiprocessing`, and the oslo case in [`corpus/real/`](../../corpus/real/) reproduces one.

## Gotchas

- **Never unpickle data from an untrusted source.** It's code execution, not only data loading.
- **"Picklable" depends on importability.** A function defined at the REPL or in a `__main__` that the other side can't import fails to unpickle.
- **Some objects are tied to this process:** locks, open files, sockets, generators. Pickling them raises `TypeError`.
- **Changing a class changes old pickles.** Unpickling uses the *current* class definition.

## Try it

```text
>>> import pickle
>>> data = pickle.dumps({"job": "add", "args": [2, 3]})
>>> type(data), len(data)
(<class 'bytes'>, 43)
>>> pickle.loads(data)
{'job': 'add', 'args': [2, 3]}
>>> import math
>>> pickle.dumps(math.sqrt)
b'\x80\x05\x95\x11\x00\x00\x00\x00\x00\x00\x00\x8c\x04math\x94\x8c\x04sqrt\x94\x93\x94.'
>>> pickle.loads(pickle.dumps(math.sqrt)) is math.sqrt
True
>>> class Point:
...     def __init__(self, x, y):
...         self.x, self.y = x, y
...     def __reduce__(self):
...         return (Point, (self.x, self.y))
... 
>>> p = Point(1, 2)
>>> p.__reduce__()
(<class '__console__.Point'>, (1, 2))
>>> import copyreg
>>> pickle.DEFAULT_PROTOCOL, pickle.HIGHEST_PROTOCOL
(5, 5)
>>> import threading
>>> pickle.dumps(threading.Lock())
Traceback (most recent call last):
  File "<console>", line 1, in <module>
TypeError: cannot pickle '_thread.lock' object
```

(Generated from [`snippets/pickle-and-reduce.repl`](snippets/pickle-and-reduce.repl) on Python 3.15.1.)

Look at the bytes for `math.sqrt`. You can read `math` and `sqrt` in them. The function is stored as "look up `sqrt` in module `math`", and unpickling gives back the *very same* object (`is` is True). `Point.__reduce__` says "rebuild me by calling `Point(1, 2)`".

## Check yourself

1. What does `pickle.dumps(math.sqrt)` actually contain?
   <details><summary>Answer</summary>

   The names `math` and `sqrt`, plus a few opcodes, and no code. Unpickling imports `math` and fetches `sqrt`.

   </details>
2. Why is `pickle.loads` on bytes from the network dangerous?
   <details><summary>Answer</summary>

   The bytes can describe "call this callable with these arguments" (what `__reduce__` returns), and that callable can be anything importable, such as `os.system("…")`. Loading them runs the call.

   </details>
3. What would `Point.__reduce__` need to return for an object rebuilt as `Point(x=1, y=2)`?
   <details><summary>Answer</summary>

   `(Point, (1, 2))`, a callable and a tuple of positional arguments, exactly as shown. pickle stores both, and `loads` calls `Point(1, 2)`.

   </details>
