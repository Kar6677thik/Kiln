"""FA007 positive: a socket passed through kwargs= (sockets don't pickle)."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103

import multiprocessing as mp
import socket


def work(conn):
    conn.close()


def main():
    conn = socket.socket()
    p = mp.Process(target=work, kwargs={"conn": conn})  # expect: FA007
    p.start()
    p.join()


if __name__ == "__main__":
    main()
