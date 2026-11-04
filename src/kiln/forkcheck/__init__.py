"""kiln.forkcheck: find code that only works because of the `fork` start method.

WHY THIS MODULE EXISTS
    Python 3.14 made forkserver the default start method on Linux, and code that silently
    relied on fork broke (krb5, oslo.concurrency, FSL pyfeeds). This package is a small
    static checker for those assumptions (rules FA001–FA008), with an optional dynamic
    `--confirm` that runs an entry script under each start method. It is standalone: it
    imports nothing from the rest of Kiln, so it can be read (and run) on its own.

HOW IT FITS
    kiln forkcheck PATH... ──cli.py──▶ forkcheck.main(argv)
        scanner.scan ──▶ findings ──(confirm.confirm_entry + attach)──▶ report.render_* ──▶ stdout
    Exit codes (§5.8): 0 = nothing at or above --fail-on, 1 = findings, 2 = usage error.

READ FIRST
    docs/concepts/start-methods.md, docs/concepts/static-vs-dynamic-analysis.md,
    docs/python/argparse.md

MILESTONE
    Introduced in K2.
"""

import argparse
import shlex
import sys
from pathlib import Path

from kiln.forkcheck.confirm import DEFAULT_TIMEOUT, attach, confirm_entry, reachable
from kiln.forkcheck.report import exit_code, render_json, render_text
from kiln.forkcheck.rules import Finding
from kiln.forkcheck.scanner import ScanResult, scan

__all__ = ["build_parser", "main", "scan"]

USAGE_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    """The argument parser for `kiln forkcheck` (K2 milestone spec §5.8–5.9).

    Why:
        All the options of §5.8–5.9 in one place, so --help documents them and tests can inspect the
        parser without running a scan.

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(
        prog="kiln forkcheck",
        description="Find code that only works under the 'fork' start method (rules FA001-FA008).",
        epilog=(
            "--confirm runs ENTRY under fork, forkserver and spawn. Confirmation is per entry "
            "point, not per finding: it shows THAT behaviour changes, not WHICH finding caused it."
        ),
    )
    parser.add_argument("paths", nargs="+", type=Path, metavar="PATH", help="files or folders")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--fail-on",
        choices=("error", "warning", "info"),
        default="warning",
        help="exit 1 if any finding has at least this severity (default: warning)",
    )
    parser.add_argument(
        "--exclude", action="append", default=[], metavar="GLOB", help="skip matching paths"
    )
    parser.add_argument(
        "--process-api",
        action="append",
        default=[],
        metavar="DOTTED.NAME",
        help="treat calls to this function as process creation (repeatable)",
    )
    parser.add_argument("--confirm", type=Path, metavar="ENTRY", help="script to run per method")
    parser.add_argument(
        "--confirm-args", default="", metavar="ARGS", help="arguments for ENTRY (one string)"
    )
    parser.add_argument(
        "--confirm-timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help="seconds before a run is killed (default: 60)",
    )
    return parser


def main(argv: list[str]) -> int:
    """Run the checker as `kiln forkcheck` does and return the exit code.

    Why:
        cli.py hands us the arguments after "forkcheck"; returning a code (instead of calling
        sys.exit) keeps this easy to test.

    Args:
        argv: the arguments, e.g. ["corpus/real/krb5-t_otp", "--format", "json"].

    Returns:
        0, 1 or 2 (§5.8).
    """
    parser = build_parser()
    try:
        options = parser.parse_args(argv)
    except SystemExit as stop:
        # PY: argparse calls sys.exit(2) on bad arguments (and 0 for --help). We turn that
        # back into a return value so callers and tests never see the interpreter exit.
        return stop.code if isinstance(stop.code, int) else USAGE_ERROR
    missing = [p for p in [*options.paths, options.confirm] if p is not None and not p.exists()]
    if missing:
        sys.stderr.write(f"kiln forkcheck: no such file or folder: {missing[0]}\n")
        return USAGE_ERROR
    result = scan(options.paths, options.exclude, options.process_api)
    findings = result.findings
    if options.confirm is not None:
        findings = _confirmed(result, options)
    if options.format == "json":
        sys.stdout.write(render_json(findings, result.files_scanned))
    else:
        sys.stdout.write(render_text(findings, result.files_scanned, result.parse_errors))
    return exit_code(findings, options.fail_on)


def _confirmed(result: ScanResult, options: argparse.Namespace) -> list[Finding]:
    """Run --confirm and attach its verdict to findings in files ENTRY reaches.

    Why:
        --confirm is per entry point, so its single verdict is copied onto every finding in a file
        ENTRY imports (directly or not), and onto nothing else.

    Args:
        result: the scan.
        options: the parsed arguments.

    Returns:
        The findings, with confirmations filled in where they apply.
    """
    entry: Path = options.confirm.resolve()
    confirmation = confirm_entry(
        entry,
        shlex.split(options.confirm_args),
        result.project.roots,
        options.confirm_timeout,
    )
    paths = reachable(entry, result.project.imports)
    displays = {m.display for p, m in result.project.modules.items() if p in paths}
    return attach(result.findings, confirmation, displays)
