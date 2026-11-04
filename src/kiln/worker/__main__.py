"""Entry point of a *spawned* worker: `python -m kiln.worker --fd N --config-json ...`.

WHY THIS MODULE EXISTS
    A spawned worker is a fresh interpreter: it has none of the master's memory, so everything
    it needs arrives on its command line: which inherited fd is its channel to the master, its
    id and generation, and the configuration as JSON. This module reads those, rebuilds the
    socket and the Config, and hands over to run_worker(), exactly as a forked worker would.

HOW IT FITS
    launch/spawn.py ──posix_spawn──▶ python -m kiln.worker ... ──▶ this file
        ──▶ worker/main.run_worker(app=None) (imports the app itself) ──▶ sys.exit(code)

READ FIRST
    docs/concepts/start-methods.md, docs/concepts/fd-passing.md, docs/python/runpy.md (python -m)

MILESTONE
    Introduced in K2.
"""

import argparse
import socket
import sys

from kiln.config import Config
from kiln.memory import exittrick
from kiln.worker.main import run_worker


def main(argv: list[str]) -> int:
    """Parse the spawn arguments and run the worker.

    Why:
        Unlike a forked worker, a spawned one exits *normally* (sys.exit), because it is a
        fresh interpreter with no master state to protect (ADR-028).

    Args:
        argv: the command-line arguments.

    Returns:
        The worker's exit code.
    """
    parser = argparse.ArgumentParser(prog="python -m kiln.worker")
    parser.add_argument("--fd", type=int, required=True)
    parser.add_argument("--worker-id", type=int, required=True)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--config-json", required=True)
    args = parser.parse_args(argv)
    # WHY: socket.socket(fileno=N) wraps an fd we already own (inherited from the master).
    channel = socket.socket(fileno=args.fd)
    config = Config.from_json(args.config_json)
    if config.worker.exit_via_os_exit:
        # WHY (K3 §5.6): registered before the app is imported, so it runs LAST of all atexit
        # handlers and skips only interpreter finalization
        # (docs/concepts/finalization-and-atexit.md).
        # GOTCHA: modules imported above (logging) registered their handlers earlier, so
        # those run *after* ours and are skipped; logging's only flushes, and our stderr
        # handler flushes every line anyway.
        exittrick.install()
    code = run_worker(
        channel=channel,
        worker_id=args.worker_id,
        generation=args.generation,
        config=config,
        app=None,
    )
    exittrick.set_exit_code(code)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
