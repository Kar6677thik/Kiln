"""pexpect test suite, reconstructed: the "force fork where the default became forkserver" fix.

Pattern (errata E-08): after Python 3.14, distributions patched pexpect's tests to build a
module-level multiprocessing context that uses "fork" whenever the default is "forkserver",
and to start test processes from that context. It is the *workaround*, so the code keeps
working under every default; forkcheck should report it as explicit fork reliance (FA008).
The upstream patch was not retrievable (HTTP 403 / bot protection), so this is reconstructed
from the search-result summary recorded in PROVENANCE.md.
"""

# ruff: noqa: T201  # WHY: printing is the observable behaviour this corpus case needs

import multiprocessing

if multiprocessing.get_start_method() == "forkserver":
    mp_ctx = multiprocessing.get_context("fork")
else:
    mp_ctx = multiprocessing.get_context()


def child(queue: multiprocessing.Queue) -> None:
    """Report which start method actually created this process.

    Args:
        queue: where to put the answer.
    """
    queue.put(multiprocessing.current_process().name)


def main() -> None:
    """Start one child through the forced context and print what it reports."""
    queue = mp_ctx.Queue()
    proc = mp_ctx.Process(target=child, args=(queue,), name="pexpect-child")
    proc.start()
    print("child says:", queue.get(timeout=10))
    proc.join(timeout=10)
    print("context start method:", mp_ctx.get_start_method())


if __name__ == "__main__":
    main()
