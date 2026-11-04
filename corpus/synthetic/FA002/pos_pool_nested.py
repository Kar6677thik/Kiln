"""FA002 positive: Pool.map pickles its function under *every* start method, fork included."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, ANN202, D103, T201

import multiprocessing as mp


def main():
    def double(n):
        return 2 * n

    with mp.Pool(2) as pool:  # expect: FA002
        print(pool.map(double, range(3)))


if __name__ == "__main__":
    main()
