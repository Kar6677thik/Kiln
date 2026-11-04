"""oslo.concurrency test_lockutils, distilled: nested functions used as Process targets.

Pattern preserved (errata E-06): a test method defines its subprocess target and a "locked"
helper as local (nested) functions, then passes the nested target to multiprocessing.Process.
Under fork nothing is pickled, so it works. Under forkserver/spawn the target must be pickled
by qualified name ("...<locals>.target"), which can't be looked up: the parent raises.
"""

# ruff: noqa: T201  # WHY: printing is the observable behaviour this corpus case needs

import fcntl
import multiprocessing
import tempfile
from pathlib import Path


class FileBasedLockingTestCase:
    """A plain stand-in for the unittest.TestCase in oslo.concurrency."""

    def test_lock_internally_between_processes(self) -> None:
        """Two processes take the same file lock in turn and append to a file."""
        workdir = Path(tempfile.mkdtemp(prefix="kiln-corpus-oslo-"))
        lock_path = workdir / "lock"
        out_path = workdir / "out"

        def locked(name: str) -> None:
            """Append a line while holding the lock.

            Args:
                name: who is writing.
            """
            with lock_path.open("a") as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
                with out_path.open("a") as out:
                    out.write(f"{name}\n")

        def target(name: str) -> None:
            """The subprocess body: call the locked helper.

            Args:
                name: who is writing.
            """
            locked(name)

        children = []
        for index in range(2):
            child = multiprocessing.Process(target=target, args=(f"child-{index}",))
            child.start()
            children.append(child)
        for child in children:
            child.join(timeout=10)
        print("lines written:", sorted(out_path.read_text().split()))


if __name__ == "__main__":
    FileBasedLockingTestCase().test_lock_internally_between_processes()
