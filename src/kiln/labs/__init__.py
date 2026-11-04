"""Built-in lab tasks: controllable workloads (sleep, burn CPU, crash, ...) for experiments.

WHY THIS MODULE EXISTS
    Every lab needs the same handful of predictable jobs: one that sleeps, one that burns
    CPU, one that crashes in a chosen way. Keeping them inside Kiln (and only registered
    when labs.enabled = true) means every milestone uses identical workloads.

HOW IT FITS
    worker/main.py ──if config.labs.enabled: register(app)──▶ tasks named "labs.*"
    The master never runs them (ADR-006); it only sees their names in the manifest.

READ FIRST
    docs/spec/03-jobs-and-tasks.md §7

MILESTONE
    Introduced in K1. Later milestones add tasks (K3 churn, K6 leaks, K7 cycles).
"""
