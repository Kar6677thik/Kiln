"""What can be pickled? Functions are pickled BY NAME; nested functions, lambdas and locks can't be."""

import pickle
import threading


def module_level(x):
    return x * 2


def make_nested():
    def nested(x):
        return x * 3

    return nested


for label, value in [
    ("module-level function", module_level),
    ("nested function", make_nested()),
    ("lambda", lambda x: x),
    ("threading.Lock()", threading.Lock()),
    ("dict of numbers", {"a": 1, "b": [2, 3]}),
]:
    try:
        data = pickle.dumps(value)
        print(f"{label:<22} OK   {len(data):3} bytes  contains name? {b'module_level' in data}")
    except Exception as error:
        print(f"{label:<22} FAIL {type(error).__name__}: {error}")

print("qualname of the nested function:", make_nested().__qualname__)
