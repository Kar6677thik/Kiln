"""Kiln's measurement toolkit: experiment records now, load generators and statistics later.

WHY THIS MODULE EXISTS
    Every lab in Kiln measures something (memory, latency, throughput) and those numbers must
    be saved in one consistent format so later milestones (K9) can compare them. This package
    groups all of that measurement code. This `__init__.py` only marks the folder as a package.

HOW IT FITS
    kiln.bench.records  ← K0: the ExperimentRecord format (docs/spec/10 §2)
    kiln.bench.load     ← K1: load generator
    kiln.bench.stats    ← K9: bootstrap confidence intervals

READ FIRST
    docs/spec/10-measurement-methodology.md

MILESTONE
    Introduced in K0.
"""
