"""FA006 positive: pytest's monkeypatch.setattr in the parent; the child sees the original."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import multiprocessing as mp
import platform


def plan9():
    return "Plan9"


def work():
    print(platform.system())


def test_platform(monkeypatch):
    monkeypatch.setattr(platform, "system", plan9)
    p = mp.Process(target=work)  # expect: FA006
    p.start()
    p.join()
