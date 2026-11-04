"""Executors: the different ways a worker can run jobs (K1 has only `sync`).

WHY THIS MODULE EXISTS
    How a worker runs jobs (one at a time, on threads, in subinterpreters, on an asyncio
    loop) is a choice K4 explores. A common interface lets worker/main.py stay the same.

HOW IT FITS
    executors/base.py  the Executor protocol and the job-running helper they share
    executors/sync.py  K1: run each job inline, capacity 1

READ FIRST
    docs/python/typing-protocol.md, docs/spec/01-architecture.md §2.2

MILESTONE
    Introduced in K1. K4 adds threads, interpreters and asyncio.
"""
