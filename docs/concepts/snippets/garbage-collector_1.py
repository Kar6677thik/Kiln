"""The cycle collector: thresholds, automatic collections, and three ways to "turn it off".

We create 100,000 small cycles under four settings and count how many collections START,
with a gc.callbacks hook. Then we show that gc.enable() undoes gc.disable() but not
gc.set_threshold(0), and that an explicit gc.collect() runs no matter what.
"""

import gc

started = []
gc.callbacks.append(
    lambda phase, info: started.append(info["generation"]) if phase == "start" else None
)
DEFAULT = gc.get_threshold()


def make_cycles(n: int = 100_000) -> None:
    """Create and drop n two-object cycles (garbage only the cycle collector can free)."""
    for _ in range(n):
        a, b = [], []
        a.append(b)
        b.append(a)


def run(label: str) -> None:
    """Make cycles and report how many automatic collections started."""
    started.clear()
    make_cycles()
    print(f"{label:46} automatic collections: {len(started)}")


print(
    "threshold:",
    DEFAULT,
    " is a list tracked by the GC?",
    gc.is_tracked([]),
    " an int?",
    gc.is_tracked(1),
)
run("default")
gc.disable()
run("gc.disable()")
gc.enable()  # what a library might do behind your back
run("gc.disable() then a library calls gc.enable()")
gc.set_threshold(0)
gc.enable()
run("gc.set_threshold(0), then gc.enable()")
started.clear()
gc.disable()
found = gc.collect()
print(f"explicit gc.collect() while disabled: ran {len(started)} collection, found {found} objects")
gc.set_threshold(*DEFAULT)
gc.enable()
