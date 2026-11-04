"""FA005 positive (starter): imports pos_helper_logfile, then starts processes."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN201, D103

import multiprocessing as mp

import pos_helper_logfile


def work():
    pos_helper_logfile.note("child")


if __name__ == "__main__":
    p = mp.Process(target=work)
    p.start()
    p.join()
