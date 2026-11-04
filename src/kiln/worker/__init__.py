"""The worker process: receives `run` messages, executes tasks, reports results.

WHY THIS MODULE EXISTS
    Workers are where user code runs, so that the master never does (ADR-006). Each worker
    talks to the master over its own private socket (its "channel").

HOW IT FITS
    worker/main.py        run_worker(): boot, the receive loop, recycling, exit
    worker/heartbeat.py   the thread that says "still alive" every second
    worker/executors/     how jobs are run (K1: one at a time, inline)

READ FIRST
    docs/spec/01-architecture.md §2.2, docs/spec/04-supervisor.md §3.1

MILESTONE
    Introduced in K1.
"""
