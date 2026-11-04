"""The `labs.*` tasks: deliberately simple, sometimes deliberately dangerous, workloads.

WHY THIS MODULE EXISTS
    Experiments need jobs whose behaviour we control exactly: "sleep 5 ms", "use the CPU for
    15 s", "die by SIGSEGV", "allocate 700 MiB". docs/spec/03-jobs-and-tasks.md §7 lists them.
    Several are dangerous on purpose (they crash or hold memory), which is why they are only
    registered when labs.enabled = true (ADR-021), and why each carries a `# LAB:` tag.

HOW IT FITS
    worker/main.py ──register(app)──▶ app.task(name="labs.sleep")(sleep) ... for each task
    clients ──submit{task: "labs.crash", kwargs: {how: "segv"}}──▶ a worker dies on purpose

READ FIRST
    docs/spec/03-jobs-and-tasks.md §7, docs/concepts/exit-status.md (how each crash looks),
    docs/concepts/cgroups-and-oom-killer.md (labs.alloc and the OOM lab)

MILESTONE
    Introduced in K1 (echo, sleep, burn_cpu, alloc, crash, raise, hold_gil).
"""

import ctypes
import os
import re
import signal
import time

from kiln.app import App

MIB = 1024 * 1024
# LAB: memory held on purpose by labs.alloc(hold=True), so a worker grows until it is
# recycled (max_rss_mb) or OOM-killed. Module-level on purpose (code-style §6 exception).
_HELD: list[bytearray] = []


def echo(value: object = None) -> object:
    """Return the argument unchanged.

    Args:
        value: any JSON value.

    Returns:
        The same value.
    """
    return value


def sleep(ms: float) -> float:
    """Sleep for `ms` milliseconds: a stand-in for an I/O-bound job.

    Args:
        ms: how long to sleep.

    Returns:
        The milliseconds actually slept.
    """
    started = time.monotonic()
    # WHY: time.sleep releases the GIL, so other threads in the worker (the heartbeat) run.
    time.sleep(ms / 1000)
    return (time.monotonic() - started) * 1000


def burn_cpu(ms: float) -> int:
    """Keep the CPU busy with pure-Python arithmetic for about `ms` milliseconds.

    Why:
        A CPU-bound stand-in. Pure Python means the interpreter still switches to other
        threads every few milliseconds (sys.getswitchinterval()), so heartbeats continue:
        the contrast with hold_gil in the K1 heartbeat lab.

    Args:
        ms: roughly how long to run (process CPU time).

    Returns:
        How many loop iterations ran.
    """
    deadline = time.process_time() + ms / 1000
    iterations = 0
    total = 0
    while time.process_time() < deadline:
        for number in range(1000):
            total += number * number
        iterations += 1
    return iterations


def alloc(mb: int, hold: bool = False) -> int:
    """Allocate `mb` MiB of memory; with hold=True, keep it until the worker exits.

    Why:
        The OOM lab (K1 §7a) and the max_rss recycling test need a worker that grows. The
        bytes are written to (bytearray fills them with zeros), so they really occupy RAM.

    Args:
        mb: how many MiB to allocate.
        hold: keep the memory referenced after the job ends (a deliberate leak).

    Returns:
        The number of bytes allocated.
    """
    block = bytearray(mb * MIB)
    if hold:
        _HELD.append(block)  # LAB: deliberate leak
    return len(block)


def crash(how: str) -> None:
    """Kill the worker in the requested way, to test death classification (spec/04 §4.1).

    Why:
        Each mode produces a different wait status: exit code 1, death by SIGSEGV, SIGABRT or
        a self-inflicted SIGKILL (which looks exactly like an OOM kill, minus the evidence).

    Args:
        how: "exit1", "segv", "abort" or "sigkill_self".

    Raises:
        ValueError: for an unknown mode.
    """
    if how == "exit1":
        os._exit(1)  # LAB: exits without cleanup, exit code 1
    if how == "segv":
        # LAB: reading memory address 0 makes the CPU fault; the kernel sends SIGSEGV.
        ctypes.string_at(0)
    if how == "abort":
        os.abort()  # LAB: raises SIGABRT, as a failed C assertion would
    if how == "sigkill_self":
        os.kill(os.getpid(), signal.SIGKILL)  # LAB: indistinguishable from an OOM kill
    raise ValueError(f"unknown crash mode {how!r}; use exit1, segv, abort or sigkill_self")


def fail(message: str = "deliberate failure") -> None:
    """Raise RuntimeError: the job fails with error kind "exception".

    Args:
        message: the exception message.

    Raises:
        RuntimeError: always.
    """
    raise RuntimeError(message)


def hold_gil(seconds: float, method: str = "pydll") -> str:
    """Block every Python thread of this process by running C code that keeps the GIL.

    Why:
        The K1 heartbeat lab needs a truly hung worker (ADR-030): alive, but unable to run
        *any* Python thread, including the heartbeat thread. Normal ctypes calls release the
        GIL around C code; ctypes.PyDLL keeps it held. The regex method is a fallback: a
        catastrophically backtracking pattern runs entirely inside the C regex engine.

    Args:
        seconds: roughly how long to hold the GIL.
        method: "pydll" (libc sleep with the GIL held) or "regex".

    Returns:
        The method used.

    Raises:
        ValueError: for an unknown method.
    """
    if method == "pydll":
        # LAB: PyDLL(None) = the C library of this process, called WITHOUT releasing the GIL.
        libc = ctypes.PyDLL(None)
        libc.sleep(int(seconds))
        return method
    if method == "regex":
        # LAB: each extra "a" roughly doubles the matching time; 30 is calibrated in
        # labs/k1/gil_hold_probe.py (verification gate G1.6).
        length = max(20, min(34, 20 + int(seconds)))
        re.match(r"(a+)+$", "a" * length + "b")
        return method
    raise ValueError(f"unknown method {method!r}; use pydll or regex")


def register(app: App) -> None:
    """Register every labs.* task on the given app.

    Why:
        Called by the worker only when labs.enabled = true, so production-style runs never
        expose tasks that crash workers on purpose (ADR-021).

    Args:
        app: the user's app.
    """
    app.task(name="labs.echo")(echo)
    app.task(name="labs.sleep")(sleep)
    app.task(name="labs.burn_cpu")(burn_cpu)
    app.task(name="labs.alloc")(alloc)
    app.task(name="labs.crash")(crash)
    app.task(name="labs.raise")(fail)
    app.task(name="labs.hold_gil")(hold_gil)
