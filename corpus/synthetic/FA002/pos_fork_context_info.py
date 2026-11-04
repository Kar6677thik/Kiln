"""FA002 known false positive: an explicit fork context works, so it is reported as info."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, ANN202, D103, T201

import multiprocessing as mp


def main():
    def target():
        print("in child")

    ctx = mp.get_context("fork")  # expect: FA008
    p = ctx.Process(target=target)  # expect: FA002
    p.start()
    p.join()


if __name__ == "__main__":
    main()
