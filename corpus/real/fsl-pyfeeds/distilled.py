"""FSL pyfeeds, reconstructed: logging configured only in the parent's main().

Pattern (errata E-07; the merge request was unreachable, so this is reconstructed from the
documented symptom "logging handlers relied on fork"):
- main(), run under the __main__ guard, configures logging with basicConfig;
- each test runs in a child Process whose target logs at INFO via getLogger(__name__).
Under fork the child inherits the parent's handler and the lines appear. Under
forkserver/spawn the child has no handler: INFO records are silently dropped.
"""

import logging
import multiprocessing

log = logging.getLogger(__name__)


def run_test(name: str) -> None:
    """Run one (pretend) feed test and log its progress.

    Args:
        name: the test name.
    """
    log.info("running test %s", name)
    log.info("test %s passed", name)


def main() -> None:
    """Configure logging for the run, then run every test in its own process."""
    logging.basicConfig(level=logging.INFO, format="PYFEEDS %(levelname)s %(message)s")
    log.info("starting 2 tests")
    for name in ("fslmaths", "bet"):
        child = multiprocessing.Process(target=run_test, args=(name,))
        child.start()
        child.join(timeout=10)
    log.info("all tests finished")


if __name__ == "__main__":
    main()
