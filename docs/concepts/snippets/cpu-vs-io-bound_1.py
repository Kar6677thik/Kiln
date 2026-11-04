"""Cooperative cancellation: asyncio.timeout can only stop a task at an `await`.

Three tasks get a 0.1 s timeout:
  polite   awaits asyncio.sleep(1): cancelled at the await, after ~0.1 s
  stubborn catches CancelledError and keeps going: the timeout can't stop it
  blocker  calls time.sleep(0.5) without awaiting: nothing can interrupt it until it ends
"""

import asyncio
import time


async def polite() -> str:
    """Waits cooperatively."""
    await asyncio.sleep(1)
    return "finished"


async def stubborn() -> str:
    """Swallows the cancellation (a bug)."""
    try:
        await asyncio.sleep(1)
    except asyncio.CancelledError:
        await asyncio.sleep(0.3)  # keeps working after being told to stop
        return "ignored the cancel"
    return "finished"


async def blocker() -> str:
    """Blocks the whole event loop: there is no await to cancel at."""
    time.sleep(0.5)
    return "finished"


async def try_with_timeout(task: object) -> None:
    """Run one task under a 0.1 s asyncio.timeout and report what happened."""
    started = time.perf_counter()
    try:
        async with asyncio.timeout(0.1):
            outcome = await task()  # type: ignore[operator]
    except TimeoutError:
        outcome = "TimeoutError"
    elapsed = time.perf_counter() - started
    print(f"{task.__name__:9} -> {outcome:20} after {elapsed:.1f} s")  # type: ignore[attr-defined]


async def main() -> None:
    """Try the three tasks one after the other."""
    for task in (polite, stubborn, blocker):
        await try_with_timeout(task)


asyncio.run(main())
