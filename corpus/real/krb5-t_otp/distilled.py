"""krb5 t_otp.py, distilled: an unguarded test script that starts a daemon Process subclass.

Pattern preserved from krb5 (errata E-05, PR #1483):
- the script has NO `if __name__ == "__main__":` guard; the test body is top-level code;
- the daemon is a `multiprocessing.Process` subclass started at top level;
- it imports a helper (k5test_like) that registers an atexit banner on import.
Under fork this works. Under forkserver/spawn the child re-imports this script as
__mp_main__, re-runs the top-level code, and fails while bootstrapping.
"""

# ruff: noqa: T201  # WHY: printing is the observable behaviour this corpus case needs

import multiprocessing
import queue

import k5test_like


class RadiusLikeDaemon(multiprocessing.Process):
    """A tiny daemon that reports "started" on a queue and answers one request."""

    def run(self) -> None:
        """Announce readiness, then echo one request back."""
        requests, replies = self._args  # type: ignore[attr-defined]
        replies.put("started")
        replies.put(f"accept:{requests.get(timeout=5)}")


requests: multiprocessing.Queue = multiprocessing.Queue()
replies: multiprocessing.Queue = multiprocessing.Queue()
daemon = RadiusLikeDaemon(args=(requests, replies), daemon=True)
daemon.start()
try:
    print("daemon:", replies.get(timeout=5))
    requests.put("otp-token")
    print("daemon:", replies.get(timeout=5))
except queue.Empty:
    print("daemon never answered")
daemon.join(timeout=5)
k5test_like.success("OTP tests")
