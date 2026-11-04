"""FA006 positive: assigning to an imported module's attribute before starting the child."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import multiprocessing as mp
import textwrap


def short_fill(text, width=70):
    return text[:width]


def work():
    print(textwrap.fill("a long sentence"))


def main():
    textwrap.fill = short_fill
    p = mp.Process(target=work)  # expect: FA006
    p.start()
    p.join()


if __name__ == "__main__":
    main()
