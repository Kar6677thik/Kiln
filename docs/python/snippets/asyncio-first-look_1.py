# Three waits of 0.2 s: one after another, then all at once with gather().
import asyncio
import time


async def fake_request(name):
    await asyncio.sleep(0.2)  # while this one waits, the event loop runs the others
    return name


async def one_after_another():
    return [await fake_request("a"), await fake_request("b"), await fake_request("c")]


async def all_together():
    return await asyncio.gather(fake_request("a"), fake_request("b"), fake_request("c"))


for coroutine_function in (one_after_another, all_together):
    start = time.perf_counter()
    results = asyncio.run(coroutine_function())
    seconds = time.perf_counter() - start
    print(f"{coroutine_function.__name__:<18} {results}  about {round(seconds, 1)} s")
