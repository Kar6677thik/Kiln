"""Reference counting: counts go up and down, objects die at 0, and cycles never reach 0."""

import gc
import sys
import weakref


class Thing:
    """An object we can watch die (weakref.finalize runs when it is freed)."""


a = Thing()
print("refcount of a fresh object:", sys.getrefcount(a), "(1 for 'a' + 1 for the argument)")
b = a
items = [a, a]
print("after b = a and a list holding it twice:", sys.getrefcount(a))
del b, items
print("after deleting those references:", sys.getrefcount(a))

weakref.finalize(a, print, "  -> the Thing was freed")
print("del a (count reaches 0):")
del a
print("... freed immediately, no garbage collector involved")

gc.disable()
x = Thing()
x.self = x  # a cycle: x refers to itself
weakref.finalize(x, print, "  -> the cyclic Thing was freed")
print("del x (it still refers to itself, so its count stays at 1):")
del x
print("... nothing printed: refcounting alone can't free a cycle")
print("gc.collect() found", gc.collect(), "unreachable objects")
gc.enable()
