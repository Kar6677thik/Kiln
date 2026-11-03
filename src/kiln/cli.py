"""The `kiln` command-line interface (CLI): turns typed commands into function calls.

WHY THIS MODULE EXISTS
    Everything a person does with Kiln starts as a command typed in a terminal, such as
    `kiln doctor` or (from K1) `kiln serve examples.demo:app`. This module reads those words
    with the standard library's `argparse`, checks them, prints helpful `--help` text, and
    calls the right function. Keeping all command parsing here means the rest of Kiln never
    has to deal with raw text from the terminal.

HOW IT FITS
    terminal ──"kiln doctor --json"──▶ main(argv)
                                        │ build_parser() → argparse
                                        ▼
                                   _cmd_doctor(args) ──calls──▶ kiln.doctor.run_checks()
    The full command list (most arrive in K1+) is in docs/spec/05-configuration-and-cli.md §4.

READ FIRST
    docs/python/argparse.md, docs/python/functions.md, docs/spec/05-configuration-and-cli.md §4

MILESTONE
    Introduced in K0 (`--version`, `doctor`). Later milestones add subcommands.
"""

import argparse
import platform
import sys

# PY: importing our own package's modules looks exactly like importing the standard library.
from kiln import __version__, doctor


def _out(text: str) -> None:
    """Print one line of user-facing output.

    Why:
        All normal CLI output goes through one function (docs/standards/code-style.md §5),
        so later milestones can redirect or capture it in one place.

    Args:
        text: the line to print.
    """
    print(text)


def version_text() -> str:
    """Build the text shown by `kiln --version`.

    Why:
        Kiln is tested on several interpreters, including the free-threaded 3.15t. When you
        report a result, the version line should say which Python and which build produced it.

    Returns:
        A line such as `kiln 0.1.0 (CPython 3.15.1, gil build)`.

    Example:
        >>> version_text().startswith("kiln 0.1.0 (CPython ")
        True
    """
    # PY: an f-string builds text with values inserted in {braces}. See docs/python/f-strings.md.
    build = "freethreaded" if doctor.is_free_threaded_build() else "gil"
    python = f"{platform.python_implementation()} {platform.python_version()}"
    return f"kiln {__version__} ({python}, {build} build)"


def build_parser() -> argparse.ArgumentParser:
    """Describe every command, flag and help text of the `kiln` CLI.

    Why:
        argparse needs a full description of the accepted arguments before it can parse them.
        Building it in its own function lets tests inspect the parser without running anything.

    Returns:
        A configured ArgumentParser with one sub-parser per subcommand.
    """
    parser = argparse.ArgumentParser(
        prog="kiln",
        description=(
            "Kiln: a pre-fork job server built to learn Python internals. "
            "Run `kiln <command> --help` for help on one command."
        ),
    )
    # WHY: argparse's built-in "version" action prints the text and exits with code 0.
    parser.add_argument(
        "--version", action="version", version=version_text(), help="show Kiln and Python versions"
    )
    # WHY: subcommands (like "git commit" vs "git push") each get their own flags and help.
    subcommands = parser.add_subparsers(dest="command", metavar="<command>")

    doctor_parser = subcommands.add_parser(
        "doctor",
        help="check that this machine can run Kiln and its labs",
        description=(
            "Inspect this machine and report what Kiln needs. Each check prints a status: "
            "✅ pass, ⚠️ warn (not needed yet, or optional), ❌ fail (must fix), "
            "ℹ️ info (a fact, no action needed). Exit code 1 if any check fails, otherwise 0."
        ),
        epilog=(
            "Example: `kiln doctor --milestone K4` asks 'am I ready for K4?': every warning "
            "about something K4 or an earlier milestone needs becomes a failure."
        ),
    )
    doctor_parser.add_argument(
        "--json", action="store_true", help="print the results as JSON instead of a table"
    )
    doctor_parser.add_argument(
        "--milestone",
        metavar="Kn",
        help="treat warnings needed by this milestone (or earlier) as failures, e.g. K1",
    )
    return parser


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Run `kiln doctor` and print its report.

    Why:
        Separates "what the user typed" (args) from "what the checks do" (kiln.doctor),
        so the checks can be unit-tested with a fake /proc tree.

    Args:
        args: parsed arguments; uses `args.json` and `args.milestone`.

    Returns:
        0 if no check failed, 1 otherwise (docs/spec/07-environment.md §12).
    """
    try:
        milestone = doctor.parse_milestone(args.milestone) if args.milestone else None
    except ValueError as error:
        # WHY: a bad --milestone value is a usage error, so we use argparse's exit code 2.
        print(f"kiln doctor: {error}", file=sys.stderr)
        return 2
    results = doctor.run_checks(doctor.DoctorContext.real(), milestone=milestone)
    if args.json:
        _out(doctor.render_json(results))
    else:
        _out(doctor.render_table(results))
    return doctor.exit_code(results)


def main(argv: list[str] | None = None) -> int:
    """Run the `kiln` command with the given arguments.

    Why:
        This is the single function behind both the `kiln` command (pyproject.toml
        [project.scripts]) and `python -m kiln`. Accepting argv as a parameter lets tests call
        it directly: `main(["doctor", "--json"])`.

    Args:
        argv: the arguments after the program name. None means "use sys.argv[1:]".

    Returns:
        The exit code for the operating system (0 success, 1 failure, 2 usage error).
    """
    parser = build_parser()
    # WHY: parse_args() prints an error and exits with code 2 by itself on bad usage.
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _cmd_doctor(args)
    # WHY: no subcommand given. Show help instead of doing nothing silently.
    parser.print_help()
    return 2
