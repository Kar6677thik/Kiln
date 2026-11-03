# Run with: python docs/python/snippets/context-managers_1.py
# Shows that "with" runs its cleanup even when the block fails.
from contextlib import contextmanager


@contextmanager
def opened(name):
    print("open", name)
    try:
        yield name
    finally:
        print("close", name)


try:
    with opened("socket") as s:
        print("using", s)
        raise RuntimeError("something went wrong")
except RuntimeError as err:
    print("caught:", err)
