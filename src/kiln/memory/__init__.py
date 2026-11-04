"""Memory measurement: reading a process's memory numbers from Linux's /proc.

WHY THIS MODULE EXISTS
    Worker recycling (max_rss_mb), the OOM lab and the K3 copy-on-write lab all need to know
    how much memory a process uses. Linux publishes that in /proc/<pid>/... text files; this
    package parses them into numbers.

HOW IT FITS
    memory/procfs.py  ← K1: /proc/<pid>/status (RSS); K3 adds smaps_rollup (PSS/USS)
    Used by worker/heartbeat.py, worker/main.py (recycling) and labs.

READ FIRST
    docs/concepts/memory-metrics.md

MILESTONE
    Introduced in K1.
"""
