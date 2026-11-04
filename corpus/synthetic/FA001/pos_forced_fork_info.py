"""FA001 known false positive: fork is forced first, so FA001 is downgraded to info (+FA008)."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import multiprocessing as mp


def work():
    print("hi")


mp.set_start_method("fork")  # expect: FA008
p = mp.Process(target=work)  # expect: FA001
p.start()
p.join()
