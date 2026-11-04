# requires: py>=3.14
"""What can be sent into a subinterpreter, and what does it see?

InterpreterPoolExecutor runs each call in another interpreter of the SAME process.
"""

import os
import threading
from concurrent.futures import InterpreterPoolExecutor

LIMIT = 10
print(f"module body runs here with __name__ = {__name__!r}", flush=True)


def stateless(x: int) -> int:
    """No globals, no closure: can be copied into the other interpreter as code."""
    return x + 1


def uses_global() -> int:
    """Reads a module global: sent by name (pickle) and found by re-running this script."""
    return LIMIT * 2


def main() -> None:
    """Submit several callables and report what happens to each."""
    global LIMIT  # noqa: PLW0603  # WHY: change the global at run time, to see who notices
    LIMIT = 1000
    offset = 5
    cases = {
        "lambda: 1": (lambda: 1, ()),
        "stateless(41)": (stateless, (41,)),
        "uses_global()": (uses_global, ()),
        "closure lambda: offset": (lambda: offset, ()),
        "stateless(a threading.Lock)": (stateless, (threading.Lock(),)),
        "os.getpid": (os.getpid, ()),
    }
    with InterpreterPoolExecutor(1) as pool:
        for name, (func, args) in cases.items():
            try:
                value = pool.submit(func, *args).result()
                if func is os.getpid:
                    value = "same process" if value == os.getpid() else "another process"
                print(f"{name:28} -> {value!r}", flush=True)
            except Exception as error:  # noqa: BLE001  # WHY: show every failure kind
                print(f"{name:28} -> {type(error).__name__}", flush=True)
    print(f"main interpreter: uses_global() = {uses_global()}", flush=True)


if __name__ == "__main__":
    main()
