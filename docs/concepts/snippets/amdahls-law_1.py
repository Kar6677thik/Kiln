"""Amdahl's law: the speed-up from N workers when a fraction of the work is serial."""


def speedup(serial_fraction: float, workers: int) -> float:
    """1 / (s + (1 - s) / N)."""
    return 1 / (serial_fraction + (1 - serial_fraction) / workers)


print("serial  N=1    N=2    N=4    N=8    N=14   limit (N -> infinity)")
for s in (0.0, 0.05, 0.10, 0.25, 0.50):
    row = "  ".join(f"{speedup(s, n):5.2f}" for n in (1, 2, 4, 8, 14))
    limit = "inf" if s == 0 else f"{1 / s:.1f}"
    print(f"{s:5.0%}   {row}   {limit}")
