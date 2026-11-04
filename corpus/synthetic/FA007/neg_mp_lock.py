"""FA007 negative: multiprocessing.Lock is designed to be passed to a Process."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, T201

import multiprocessing as mp


def work(lock):
    with lock:
        print("locked")


def main():
    lock = mp.Lock()
    p = mp.Process(target=work, args=(lock,))
    p.start()
    p.join()


if __name__ == "__main__":
    main()
