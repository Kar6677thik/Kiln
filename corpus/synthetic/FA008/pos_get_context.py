"""FA008 positive: get_context("fork") (pexpect's fix)."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103, T201

import multiprocessing as mp


def work():
    print("child")


def main():
    ctx = mp.get_context("fork")  # expect: FA008
    p = ctx.Process(target=work)
    p.start()
    p.join()


if __name__ == "__main__":
    main()
