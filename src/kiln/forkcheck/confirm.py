"""`--confirm`: run the entry script under each start method and see whether behaviour changes.

WHY THIS MODULE EXISTS
    Static rules guess; running the code knows. A finding is much more convincing when the
    same script demonstrably behaves differently under spawn or forkserver than under fork.
    This module runs ENTRY three times (once per start method), each in a fresh Python process
    in its own process group with a timeout, records an outcome per method, and marks findings
    in files ENTRY imports as confirmed when spawn or forkserver differ from the fork baseline.
    Limitation: confirmation is per entry point, not per finding. It shows *that* behaviour
    changes, not *which* finding caused it (docs/concepts/static-vs-dynamic-analysis.md).

HOW IT FITS
    forkcheck.main ──--confirm ENTRY──▶ confirm_all(entry, args, roots, timeout)
        for method in (fork, forkserver, spawn): subprocess ──▶ MethodRun(outcome, output)
    attach(findings, result, reachable files) ──▶ findings with .confirmation filled in

READ FIRST
    docs/concepts/start-methods.md, docs/concepts/main-module.md, docs/python/runpy.md

MILESTONE
    Introduced in K2.
"""

import ast
import dataclasses
import os
import re
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from kiln.forkcheck.rules import Finding

# CONCEPT(start-methods): fork copies the parent's memory; forkserver and spawn start a fresh
# interpreter that re-imports the main module and receives the target by pickling.
METHODS = ("fork", "forkserver", "spawn")
DEFAULT_TIMEOUT = 60.0

# CONCEPT(main-module): runpy.run_path(..., run_name="__main__") runs ENTRY as the main
# module *and* leaves sys.modules["__main__"].__file__ pointing at ENTRY while it runs, so a
# spawn/forkserver child can find the file and re-import it as "__mp_main__" (gate G2.5).
BOOTSTRAP = (
    "import multiprocessing as mp, runpy, sys\n"
    "mp.set_start_method({method!r}, force=True)\n"
    "sys.argv = [{entry!r}, *{args!r}]\n"
    "runpy.run_path({entry!r}, run_name='__main__')\n"
)
COPY_PREAMBLE = (
    "import multiprocessing as _kiln_confirm_mp\n"
    "_kiln_confirm_mp.set_start_method({method!r}, force=True)\n"
)

HEX = re.compile(r"0x[0-9a-fA-F]+")
PID = re.compile(r"\b(pid[ =:]*)\d+", re.IGNORECASE)
TIMING = re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds)\b")
EXCEPTION_LINE = re.compile(r"^([A-Za-z_][\w.]*)(?::|$)")


@dataclass(frozen=True, slots=True)
class MethodRun:
    """The result of running ENTRY under one start method.

    Why:
        The outcome string is what users see; the normalised output is what `differs` compares.
    """

    method: str
    outcome: str  # "ok", "exception:<Type>", "signal:<NAME>", "exit:<code>" or "timeout"
    output: str  # normalised stdout + stderr


@dataclass(frozen=True, slots=True)
class Confirmation:
    """All three runs plus the verdict.

    Why:
        `differs` is the one bit a finding needs; the per-method outcomes explain it.
    """

    runs: dict[str, MethodRun]
    differs: bool

    def as_dict(self) -> dict[str, object]:
        """The JSON shape of §5.9: {"fork": ..., "forkserver": ..., "spawn": ..., "differs": ...}.

        Returns:
            A dict ready for json.dumps.
        """
        result: dict[str, object] = {m: self.runs[m].outcome for m in METHODS}
        result["differs"] = self.differs
        return result


def normalise(text: str) -> str:
    """Replace volatile parts of output (addresses, pids, timings) with placeholders.

    Why:
        Two runs of the same program print different pids and addresses; without this every
        run would "differ". The placeholders match the snippet runner's (docs/spec/09 §7).

    Args:
        text: raw output.

    Returns:
        The text with 0x... → <ADDR>, "pid 123" → "pid <PID>", "12.5 ms" → <MS>.
    """
    text = HEX.sub("<ADDR>", text)
    text = PID.sub(r"\1<PID>", text)
    return TIMING.sub("<MS>", text)


def outcome_of(returncode: int, stderr: str) -> str:
    """Turn a return code and stderr into an outcome string.

    Why:
        A Python exception exits with code 1; the *type* (PicklingError, AttributeError…) is
        far more useful, so it is parsed from the last line of the traceback.

    Args:
        returncode: the subprocess's return code (negative = killed by that signal).
        stderr: its standard error.

    Returns:
        "ok", "signal:<NAME>", "exception:<Type>" or "exit:<code>".
    """
    if returncode == 0:
        return "ok"
    if returncode < 0:
        try:
            return f"signal:{signal.Signals(-returncode).name}"
        except ValueError:
            return f"signal:{-returncode}"
    if "Traceback (most recent call last):" in stderr:
        for line in reversed(stderr.strip().splitlines()):
            match = EXCEPTION_LINE.match(line)
            if match is not None and not line.startswith(" "):
                # WHY: keep the last dotted part, so "_pickle.PicklingError" → "PicklingError".
                return f"exception:{match.group(1).rsplit('.', 1)[-1]}"
    return f"exit:{returncode}"


def insertion_line(source: str) -> int:
    """Where the start-method preamble goes in the temp copy.

    It goes after the module docstring and any `from __future__` imports: those must stay
    first, or Python rejects the file.

    Why:
        A `from __future__` import must be the first statement (after the docstring), so the
        preamble can't simply go on line 1.

    Args:
        source: the entry script's source.

    Returns:
        The 0-based line index to insert before.
    """
    tree = ast.parse(source)
    after = 0
    for index, stmt in enumerate(tree.body):
        is_docstring = (
            index == 0
            and isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        )
        is_future = isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__"
        if not (is_docstring or is_future):
            break
        after = stmt.end_lineno or stmt.lineno
    return after


def make_copy_source(source: str, method: str) -> str:
    """The temp-copy fallback's source: the original with the preamble inserted.

    Args:
        source: the entry script's source.
        method: the start method.

    Returns:
        The new source.
    """
    lines = source.splitlines(keepends=True)
    cut = insertion_line(source)
    if cut and lines and not lines[cut - 1].endswith("\n"):
        lines[cut - 1] += "\n"
    return "".join(lines[:cut]) + COPY_PREAMBLE.format(method=method) + "".join(lines[cut:])


def _environment(roots: list[Path]) -> dict[str, str]:
    """The child's environment: ours, plus the scanned roots on PYTHONPATH.

    Why:
        The entry script may import helpers that sit in the scanned roots; putting the roots on
        PYTHONPATH lets the child (and its children) find them.

    Args:
        roots: the scanned folders.

    Returns:
        The environment dict.
    """
    env = dict(os.environ)
    paths = [str(r) for r in roots]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    return env


def _run(command: list[str], cwd: Path, env: dict[str, str], timeout: float) -> tuple[str, str]:
    """Run a command in its own process group, killing the whole group on timeout.

    Why:
        The script under test may start children (that is the point) and may hang. Killing
        just the direct child would leave its children running; killing the *group* doesn't.

    Args:
        command: argv.
        cwd: working directory.
        env: environment.
        timeout: seconds before the group is killed.

    Returns:
        (outcome, raw combined output).
    """
    # PY: start_new_session=True makes the child call setsid(): it leads a new session and
    # process group, so os.killpg(child.pid, ...) reaches it and everything it starts.
    # WHY (noqa S603): the command is our own interpreter plus a script the user named.
    child = subprocess.Popen(  # noqa: S603
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=True,
    )
    try:
        stdout, stderr = child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = child.communicate()
        return "timeout", stdout + stderr
    try:
        # WHY: the leader exited, but grandchildren (e.g. a stray daemon) may still be alive.
        os.killpg(child.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    return outcome_of(child.returncode, stderr), stdout + stderr


def run_method(
    entry: Path,
    method: str,
    args: list[str],
    roots: list[Path],
    timeout: float = DEFAULT_TIMEOUT,
    mode: str = "runpy",
) -> MethodRun:
    """Run ENTRY once under `method`, with the runpy bootstrap or the temp-copy fallback.

    Why:
        Each run is a fresh interpreter, so the start method chosen is the only difference
        between the three runs.

    Args:
        entry: the script to run (absolute path).
        method: "fork", "forkserver" or "spawn".
        args: extra command-line arguments for the script.
        roots: folders to put on PYTHONPATH.
        timeout: seconds before the run is killed.
        mode: "runpy" (the bootstrap) or "copy" (the §5.9 fallback).

    Returns:
        The MethodRun.
    """
    env = _environment(roots)
    if mode == "runpy":
        code = BOOTSTRAP.format(method=method, entry=str(entry), args=list(args))
        outcome, output = _run([sys.executable, "-c", code], entry.parent, env, timeout)
        return MethodRun(method, outcome, normalise(output))
    copy = entry.parent / f"_kiln_confirm_{entry.stem}.py"
    source = entry.read_text(encoding="utf-8")
    copy.write_bytes(make_copy_source(source, method).encode("utf-8"))
    try:
        outcome, output = _run([sys.executable, str(copy), *args], entry.parent, env, timeout)
    finally:
        copy.unlink(missing_ok=True)
    return MethodRun(method, outcome, normalise(output))


def confirm_entry(
    entry: Path,
    args: list[str],
    roots: list[Path],
    timeout: float = DEFAULT_TIMEOUT,
    mode: str = "runpy",
) -> Confirmation:
    """Run ENTRY under all three start methods and compare with the fork baseline.

    Why:
        Fork is the baseline because the code under test was written for it; any change under spawn
        or forkserver is what the findings predict.

    Args:
        entry: the script to run.
        args: its arguments.
        roots: folders to put on PYTHONPATH.
        timeout: seconds per run.
        mode: "runpy" or "copy".

    Returns:
        The Confirmation (differs = spawn or forkserver differs from fork).
    """
    entry = entry.resolve()
    runs = {m: run_method(entry, m, args, roots, timeout, mode) for m in METHODS}
    base = runs["fork"]
    # CONCEPT(static-vs-dynamic-analysis): running the code can show *that* behaviour changes
    # (no false alarm), but only for the paths this one run takes, and not *which* finding
    # caused it. The static rules see every path, but they can be wrong.
    differs = any(
        runs[m].outcome != base.outcome or runs[m].output != base.output
        for m in ("spawn", "forkserver")
    )
    return Confirmation(runs, differs)


def reachable(entry: Path, imports: dict[Path, set[Path]]) -> set[Path]:
    """Every scanned file ENTRY imports, directly or indirectly, plus ENTRY itself.

    Why:
        Only findings in code ENTRY actually runs can be confirmed by running it.

    Args:
        entry: the entry script.
        imports: the scanner's import links (module path → paths it imports).

    Returns:
        The reachable paths.
    """
    start = entry.resolve()
    seen = {start}
    frontier = [start]
    while frontier:
        current = frontier.pop()
        for target in imports.get(current, set()):
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    return seen


def attach(
    findings: list[Finding], confirmation: Confirmation, reachable_displays: set[str]
) -> list[Finding]:
    """Copy the confirmation onto every finding in a file ENTRY reaches.

    Args:
        findings: the scan's findings.
        confirmation: the result of confirm_entry.
        reachable_displays: display paths of the files ENTRY reaches.

    Returns:
        New findings (Finding is frozen, so changed ones are copies).
    """
    data = confirmation.as_dict()
    return [
        dataclasses.replace(f, confirmation=data) if f.path in reachable_displays else f
        for f in findings
    ]
