"""Stand-in for krb5's k5test helper module, distilled (written fresh, not copied).

The real k5test registers an exit handler at import time. If the test script never called
success(), the handler prints a "re-run this test" failure banner. Importing it is a side
effect: every process that imports it registers its own handler.
"""

# ruff: noqa: T201  # WHY: printing is the observable behaviour this corpus case needs

import atexit
import sys

_state = {"succeeded": False}


def _final_banner() -> None:
    """Print the failure banner unless success() was called in this process."""
    if not _state["succeeded"]:
        print("*** Failure: test script did not complete; re-run this test", file=sys.stderr)


atexit.register(_final_banner)  # import-time side effect (the krb5 pattern, errata E-05)


def success(name: str) -> None:
    """Mark the test as passed (silences the banner) and say so.

    Args:
        name: the test name.
    """
    _state["succeeded"] = True
    print(f"*** Success: {name}")
