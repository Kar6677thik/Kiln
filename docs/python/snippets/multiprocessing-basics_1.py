"""Just enough multiprocessing to read the K2 corpus: Process, Pool, get_context, start methods."""

import multiprocessing as mp
import os


def square(n):
    return n * n


def greet(name):
    print(f"  hello {name} from pid-is-different={os.getpid() != PARENT_PID}", flush=True)


PARENT_PID = os.getpid()

if __name__ == "__main__":
    print("default start method here:", mp.get_start_method())
    for method in ("fork", "spawn"):
        context = mp.get_context(method)
        process = context.Process(target=greet, args=(method,))
        process.start()
        process.join()
        print(f"  {method}: child exit code {process.exitcode}")
    with mp.get_context("spawn").Pool(processes=2) as pool:
        print("Pool.map(square, [1, 2, 3, 4]) =", pool.map(square, [1, 2, 3, 4]))
