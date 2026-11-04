"""The master process ("arbiter"): owns the job queue and supervises the workers.

WHY THIS MODULE EXISTS
    One process must know everything: which jobs exist, which worker runs which job, which
    workers are alive. That's the master. It is single-threaded and never blocks (ADR-006).

HOW IT FITS
    arbiter.py      the event loop, wiring everything together
    jobs.py         the job table and its state machine
    timers.py       deadlines (job timeouts, heartbeat checks...)
    connections.py  non-blocking sockets with input/output buffers
    signals.py      turning signals into events for the loop
    workers.py      the master's record of each worker
    dispatcher.py   handing queued jobs to free worker slots
    supervisor.py   launching, reaping, classifying, recycling and reloading workers

READ FIRST
    docs/spec/01-architecture.md §4, docs/spec/04-supervisor.md, docs/concepts/event-loops.md

MILESTONE
    Introduced in K1.
"""
