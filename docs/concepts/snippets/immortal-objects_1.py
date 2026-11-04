# requires: py>=3.14
"""Which objects are immortal (PEP 683)? sys._is_immortal exists from Python 3.14.

An immortal object's refcount is never changed, so reading it never writes to its memory.
"""

import sys

built = "".join(["kiln", "-", "worker"])  # a str made at run time
candidates = [
    ("None, True, ()", (None, True, ())),
    ("small int 7", 7),
    ("int 1000 (made at run time)", int("1000")),
    ("int 5000 (made at run time)", int("5000")),
    ("float 1.5 (made at run time)", float("1.5")),
    ("str '' (empty)", ""),
    ("str made at run time", built),
    ("sys.intern() of a new str", sys.intern("".join(["kiln", "-", "x"]))),
    ("sys.intern() of '__init__'", sys.intern("".join(["__", "init", "__"]))),
    ("a new list", []),
    ("a new dict", {}),
]
build = "free-threaded" if not sys._is_gil_enabled() else "GIL"
print(f"Python {sys.version.split()[0]} ({build} build)")
for label, obj in candidates:
    if isinstance(obj, tuple) and label.startswith("None"):
        result = all(sys._is_immortal(o) for o in obj)
    else:
        result = sys._is_immortal(obj)
    print(f"  {label:34} immortal: {result}")
print("refcount reported for None:", sys.getrefcount(None))
