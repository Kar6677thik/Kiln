"""FA008 negative: choosing spawn is not fork reliance."""

import multiprocessing as mp

if __name__ == "__main__":
    mp.set_start_method("spawn")
