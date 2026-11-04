"""A tiny example Kiln app: run it with `kiln serve examples.demo:app --labs`.

WHY THIS MODULE EXISTS
    Kiln runs *your* functions; this file shows the smallest possible app, and gives tests
    and labs a stable app to serve (docs/spec/03-jobs-and-tasks.md §2).

HOW IT FITS
    kiln serve examples.demo:app ──load_app──▶ this module's `app` ──▶ tasks add, upper, slow

READ FIRST
    docs/spec/03-jobs-and-tasks.md §2, docs/python/decorators.md

MILESTONE
    Introduced in K1.
"""

import time

import kiln

app = kiln.App("demo")


@app.task
def add(a: int, b: int) -> int:
    """Add two numbers.

    Args:
        a: first number.
        b: second number.

    Returns:
        The sum.
    """
    return a + b


@app.task(name="text.upper", timeout_s=5)
def upper(text: str) -> str:
    """Upper-case a string (registered under a custom name, with a 5 s timeout).

    Args:
        text: any text.

    Returns:
        The text in capitals.
    """
    return text.upper()


@app.task(max_attempts=2, retry_on=["timeout"])
def slow(seconds: float) -> float:
    """Sleep, then return; with a short timeout it shows timeouts and one retry.

    Args:
        seconds: how long to sleep.

    Returns:
        The seconds slept.
    """
    time.sleep(seconds)
    return seconds
