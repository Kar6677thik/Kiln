"""Executors: the different ways a worker can run jobs (K1 has only `sync`).

WHY THIS MODULE EXISTS
    How a worker runs jobs (one at a time, on threads, in subinterpreters, on an asyncio
    loop) is a choice K4 explores. A common interface lets worker/main.py stay the same.

HOW IT FITS
    executors/base.py          the Executor protocol and the job-running helpers they share
    executors/sync.py          K1: run each job inline, capacity 1
    executors/threads.py       K4: a thread pool, capacity = worker.executor_concurrency
    executors/asyncio_exec.py  K4: one event loop in its own thread (cooperative timeouts)
    executors/interpreters.py  K4: a pool of subinterpreters (Python 3.14+)
    worker/main.py ──make_executor(config, app)──▶ the configured one

READ FIRST
    docs/python/typing-protocol.md, docs/spec/01-architecture.md §2.2

MILESTONE
    Introduced in K1. K4 adds threads, interpreters and asyncio.
"""

from kiln.app import App
from kiln.config import Config
from kiln.worker.executors.base import Executor


def make_executor(config: Config, app: App) -> Executor:
    """Create the executor named by worker.executor.

    Why:
        One place maps the setting to a class. The K4 modules are imported only when
        chosen: `interpreters` needs Python 3.14+ (config validation already refuses it on
        3.13, K4 §5.5).

    Args:
        config: the configuration.
        app: the loaded app.

    Returns:
        The executor.
    """
    name = config.worker.executor
    concurrency = config.worker.executor_concurrency
    if name == "threads":
        from kiln.worker.executors.threads import ThreadsExecutor

        return ThreadsExecutor(app, concurrency)
    if name == "asyncio":
        from kiln.worker.executors.asyncio_exec import AsyncioExecutor

        return AsyncioExecutor(app, concurrency)
    if name == "interpreters":
        from kiln.worker.executors.interpreters import InterpretersExecutor

        return InterpretersExecutor(config, concurrency)
    from kiln.worker.executors.sync import SyncExecutor

    return SyncExecutor(app)
