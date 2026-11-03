# Classes and Objects

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [variables and types](variables-and-types.md), [functions](functions.md) · **Reading time:** ~14 min
> **Terms introduced:** **class**, **instance**, **attribute**, **method**, **`self`**, **`__init__`**, **inheritance**, **subclass**, **`super()`**, **`isinstance`**, **`AttributeError`**

## What it looks like

```python
class Worker:
    def __init__(self, worker_id, capacity):
        self.worker_id = worker_id  # data stored on this particular worker
        self.capacity = capacity
        self.jobs_done = 0

    def finish_job(self):  # behaviour every worker has
        self.jobs_done += 1
        return self.jobs_done


w = Worker(0, capacity=1)  # make one worker
w.finish_job()  # → 1
```

## What it means

A **class** is a blueprint. It describes what data a kind of thing holds and what it can do. An **instance** (also called an object) is one concrete thing built from that blueprint. `Worker` is the class, and `w` is an instance of it. You can build as many instances as you like, and each one has its own data.

Line by line:

- `class Worker:` starts the blueprint. By convention, class names use `PascalCase`.
- `def __init__(self, worker_id, capacity):` is the **initializer**, which runs automatically every time you write `Worker(...)`. Its job is to set up the new instance's data. The name has two underscores on each side; Python calls such names "dunder" (double-underscore) methods and gives them special meaning.
- **`self`** is the instance being set up or used. Python passes it in automatically: `w.finish_job()` really calls `Worker.finish_job(w)`. Calling it `self` is a strong convention, not a keyword.
- `self.worker_id = worker_id` creates an **attribute**, a named piece of data stored on that instance.
- `def finish_job(self):` is a **method**, a function that belongs to the class and works on one instance through `self`.

```
 class Worker (blueprint)               instances (each has its own data)
 ┌───────────────────────────┐         ┌───────────────────────┐
 │ __init__(self, id, cap)   │  ───▶   │ w: worker_id=0        │
 │ finish_job(self)          │         │    capacity=1         │
 └───────────────────────────┘         │    jobs_done=2        │
                                       └───────────────────────┘
                                       ┌───────────────────────┐
                                       │ other: worker_id=1    │
                                       │    capacity=8         │
                                       │    jobs_done=0        │
                                       └───────────────────────┘
```

### Inheritance and `super()`

**Inheritance** lets a new class start from an existing one. `class ThreadWorker(Worker):` makes `ThreadWorker` a **subclass** of `Worker`: it gets all of `Worker`'s methods, and can add new ones or replace existing ones. Inside the subclass, **`super().__init__(...)`** calls the parent class's version of `__init__`, so the parent's setup still happens.

**`isinstance(obj, Cls)`** asks "is `obj` an instance of `Cls`, or of a subclass of it?".

## Why people use it

Classes keep **data and the code that works on it together**. Kiln's master needs to track each worker's pid, state, free slots and in-flight jobs. A `WorkerHandle` class ([spec/04 §2](../spec/04-supervisor.md#2-workerhandle-the-masters-record-of-one-worker)) bundles all of that into one object per worker, instead of six parallel dicts that have to be kept in sync.

## How Kiln uses it

- [`scripts/check_docs.py`](../../scripts/check_docs.py) and [`src/kiln/doctor.py`](../../src/kiln/doctor.py) use small classes to hold results (a `Problem`, a `CheckResult`). Those are written as *dataclasses*, a shorter way to write data-holding classes, covered in [dataclasses](dataclasses.md).
- From K1 the master uses classes for its long-lived parts: `JobTable`, `WorkerHandle` and the `Arbiter` that owns the event loop ([spec/01 §5](../spec/01-architecture.md#5-package-layout-module-map)).
- Kiln's own errors are classes that inherit from Python's `Exception` (see [exceptions](exceptions.md)).
- Kiln's code style deliberately avoids *advanced* class features (metaclasses, descriptors) in its own code ([code-style §1](../standards/code-style.md#1-plain-python-preferred)).

## Gotchas

- **Forgetting `self`.** Writing `jobs_done += 1` instead of `self.jobs_done += 1` inside a method is an error: there's no local variable `jobs_done`.
- **Forgetting `self` in the parameter list.** `def finish_job():` fails when called, because Python still passes the instance and there's no parameter to receive it.
- **Typos in attribute names create new attributes.** `self.job_done = 1` (missing `s`) silently adds a *new* attribute. Reading a missing one raises **`AttributeError`**, as in "Try it". Type checkers like mypy (see [type hints](type-hints.md)) catch many of these.
- **Class-level mutable attributes are shared.** If you write `items = []` directly under `class X:` (not inside `__init__`), every instance shares that one list.

## Try it

```text
>>> class Worker:
...     def __init__(self, worker_id, capacity):
...         self.worker_id = worker_id
...         self.capacity = capacity
...         self.jobs_done = 0
...     def finish_job(self):
...         self.jobs_done += 1
...         return self.jobs_done
... 
>>> w = Worker(0, capacity=1)
>>> w.worker_id
0
>>> w.finish_job()
1
>>> w.finish_job()
2
>>> w.jobs_done
2
>>> other = Worker(1, capacity=8)
>>> other.jobs_done
0
>>> type(w).__name__
'Worker'
>>> isinstance(w, Worker)
True
>>> class ThreadWorker(Worker):
...     def __init__(self, worker_id, threads):
...         super().__init__(worker_id, capacity=threads)
...         self.threads = threads
...     def describe(self):
...         return "worker " + str(self.worker_id) + " with " + str(self.threads) + " threads"
... 
>>> t = ThreadWorker(2, threads=4)
>>> t.capacity
4
>>> t.finish_job()
1
>>> t.describe()
'worker 2 with 4 threads'
>>> isinstance(t, Worker)
True
>>> w.colour
Traceback (most recent call last):
  File "<console>", line 1, in <module>
AttributeError: 'Worker' object has no attribute 'colour'
```

Things to notice:
- `other.jobs_done` is `0`. Each instance has its own data.
- `t.capacity` is `4`, because `super().__init__` ran `Worker`'s setup with `capacity=threads`.
- `t.finish_job()` works although `ThreadWorker` never defined it. It's inherited.

(In the REPL, methods inside a class are typed with no blank line between them, because a blank line would end the class. In a `.py` file you put one blank line between methods.)

## Check yourself

1. In `w.finish_job()`, where does the `self` parameter come from?
   <details><summary>Answer</summary>

   Python passes the instance automatically: `w.finish_job()` is the same as `Worker.finish_job(w)`, so `self` is `w`.

   </details>
2. What's the difference between a class and an instance?
   <details><summary>Answer</summary>

   The class is the blueprint (`Worker`). An instance is one object built from it (`Worker(0, capacity=1)`), with its own attribute values.

   </details>
3. In `ThreadWorker.__init__`, what would break if you removed the `super().__init__(...)` line?
   <details><summary>Answer</summary>

   `Worker.__init__` would never run, so `worker_id`, `capacity` and `jobs_done` would never be set. Calling `t.finish_job()` would then raise `AttributeError` on `self.jobs_done`.

   </details>
