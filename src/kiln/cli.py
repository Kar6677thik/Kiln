"""The `kiln` command-line interface (CLI): turns typed commands into function calls.

WHY THIS MODULE EXISTS
    Everything a person does with Kiln starts as a command typed in a terminal, such as
    `kiln doctor` or `kiln serve examples.demo:app`. This module reads those words with the
    standard library's `argparse`, checks them, prints helpful `--help` text, and calls the
    right function. Keeping all command parsing here means the rest of Kiln never has to deal
    with raw text from the terminal.

HOW IT FITS
    terminal ──"kiln submit add --args [2,3] --wait"──▶ main(argv)
                                        │ build_parser() → argparse
                                        ▼
        _cmd_serve ──▶ master.arbiter.Arbiter(config).run()       (the server)
        _cmd_submit / _cmd_status / ... ──▶ client.KilnClient       (talk to a server)
        _cmd_reload / _cmd_stop / _cmd_scale ──▶ os.kill(pid from the pidfile, signal)
        _cmd_bench ──▶ bench.load.run_load()                        (load generator)
        _cmd_doctor ──▶ doctor.run_checks()
    The full command list is in docs/spec/05-configuration-and-cli.md §4.

READ FIRST
    docs/python/argparse.md, docs/python/functions.md, docs/spec/05-configuration-and-cli.md §4

MILESTONE
    Introduced in K0 (`--version`, `doctor`). K1 adds serve, submit, status, wait, cancel,
    stats, reload, stop, scale and bench load.
"""

import argparse
import json
import os
import platform
import signal
import sys
import time
from pathlib import Path

# PY: importing our own package's modules looks exactly like importing the standard library.
from kiln import __version__, doctor, forkcheck
from kiln.bench.load import LoadOptions, run_load
from kiln.client import KilnClient
from kiln.config import ConfigError, load_config
from kiln.errors import KilnError
from kiln.master.arbiter import Arbiter
from kiln.memory import memtable

EXIT_USAGE = 2


def _out(text: str) -> None:
    """Print one line of user-facing output.

    Why:
        All normal CLI output goes through one function (docs/standards/code-style.md §5),
        so later milestones can redirect or capture it in one place.

    Args:
        text: the line to print.
    """
    print(text)


def _err(text: str) -> None:
    """Print an error message to stderr.

    Args:
        text: the message.
    """
    print(text, file=sys.stderr)


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


def _json_arg(text: str) -> object:
    """Parse a JSON value for the --args/--kwargs options (an argparse "type" function).

    Args:
        text: the argument text.

    Returns:
        The parsed value.

    Raises:
        argparse.ArgumentTypeError: not valid JSON (argparse turns this into exit code 2).
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError(f"not valid JSON: {error}") from error


# GOTCHA: the quotes around the type below matter. Python 3.13 evaluates annotations when the
# function is defined, and _SubParsersAction can't be subscripted at run time; a string is only
# read by mypy. (3.14+ evaluates annotations lazily, PEP 649, which is why only 3.13 failed.)
def _add_server_commands(
    subcommands: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    """Add `serve`, `reload`, `stop` and `scale`.

    Why:
        Grouping the server-side commands keeps build_parser() short enough to read in one go.

    Args:
        subcommands: the subparser collection.
    """
    serve = subcommands.add_parser(
        "serve",
        help="run a Kiln master (and its workers) in the foreground",
        description=(
            "Start the master, which forks worker processes and serves KP/1 on 127.0.0.1:7340. "
            "Logs go to stderr. Ctrl-C once = graceful shutdown, twice = fast. "
            "Jobs live in memory only: stopping the master loses queued jobs (ADR-011)."
        ),
        epilog="Example: kiln serve examples.demo:app --workers 4 --labs --pidfile /tmp/kiln.pid",
    )
    serve.add_argument("app_spec", nargs="?", help="the app, as 'package.module:attribute'")
    serve.add_argument(
        "--config", type=Path, help="a kiln.toml file (default: ./kiln.toml if present)"
    )
    serve.add_argument("--workers", type=int, help="number of worker processes (master.workers)")
    serve.add_argument("--bind", help="host:port to listen on (master.bind)")
    serve.add_argument("--pidfile", help="write the master's pid here (for kiln reload/stop/scale)")
    serve.add_argument("--labs", action="store_true", help="register the labs.* experiment tasks")
    serve.add_argument("--debug", action="store_true", help="enable debug operations (K6)")
    serve.add_argument(
        "--start-method",
        choices=("fork", "spawn", "forkserver"),
        help="how workers are created (default: fork; docs/concepts/start-methods.md)",
    )
    serve.add_argument(
        "--executor", choices=("sync", "threads", "interpreters", "asyncio"), help="(K4)"
    )
    reload = subcommands.add_parser(
        "reload",
        help="replace all workers without dropping jobs (sends SIGHUP)",
        description=(
            "Gracefully replace every worker. Caveat: with fork + app.preload (the defaults), new "
            "workers are copies of the master, so CODE CHANGES ARE NOT PICKED UP; restart the "
            "master, or set app.preload = false (spec/04 §5.2)."
        ),
    )
    stop = subcommands.add_parser(
        "stop", help="shut the master down (SIGTERM; --fast sends SIGINT twice)"
    )
    stop.add_argument("--fast", action="store_true", help="don't wait for running jobs")
    scale = subcommands.add_parser(
        "scale", help="add or remove workers: kiln scale +2 / kiln scale -1"
    )
    scale.add_argument(
        "delta", type=int, help="+N adds N workers (SIGTTIN), -N removes N (SIGTTOU)"
    )
    for sub in (reload, stop, scale):
        sub.add_argument("--pidfile", required=True, help="the pidfile given to kiln serve")


def _add_client_commands(
    subcommands: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    """Add `submit`, `status`, `wait`, `cancel` and `stats`.

    Why:
        Grouping the commands that talk to a running master keeps build_parser() short enough to
        read.

    Args:
        subcommands: the subparser collection.
    """
    submit = subcommands.add_parser(
        "submit",
        help="queue a job and print its id (or its result with --wait)",
        epilog="Example: kiln submit add --args '[2, 3]' --wait",
    )
    submit.add_argument("task", help="the task name, such as add or labs.sleep")
    submit.add_argument(
        "--args", type=_json_arg, default=[], help="positional arguments, as a JSON list"
    )
    submit.add_argument(
        "--kwargs", type=_json_arg, default={}, help="keyword arguments, as a JSON object"
    )
    submit.add_argument("--timeout", type=float, help="the job's timeout in seconds")
    submit.add_argument("--max-attempts", type=int, help="how many times it may be tried")
    submit.add_argument(
        "--wait", action="store_true", help="wait for the result and print the JobView"
    )
    status = subcommands.add_parser("status", help="print a job's current state")
    status.add_argument("job_id")
    wait = subcommands.add_parser("wait", help="wait for a job to finish and print it")
    wait.add_argument("job_id")
    wait.add_argument("--timeout", type=float, default=30.0, help="seconds to wait (≤ 300)")
    cancel = subcommands.add_parser("cancel", help="cancel a job that is still queued")
    cancel.add_argument("job_id")
    subcommands.add_parser("stats", help="print the server's statistics (StatsView)")


def _add_bench_commands(subcommands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Add `bench load`.

    Why:
        Measurement tools get their own sub-group, so later milestones can add `bench matrix` (K9)
        beside `load`.

    Args:
        subcommands: the subparser collection.
    """
    bench = subcommands.add_parser("bench", help="measurement tools")
    tools = bench.add_subparsers(dest="bench_command", metavar="<tool>")
    load = tools.add_parser(
        "load",
        help="submit many jobs and record throughput, latency and outcomes",
        epilog="Example: kiln bench load --jobs 10000 --concurrency 64 --task labs.echo",
    )
    load.add_argument("--jobs", type=int, required=True)
    mode = load.add_mutually_exclusive_group()
    mode.add_argument("--concurrency", type=int, help="closed loop: C connections, back to back")
    mode.add_argument("--rate", type=float, help="open loop: start R jobs per second")
    load.add_argument("--task", default="labs.echo")
    load.add_argument("--args", type=_json_arg, default=[])
    load.add_argument(
        "--kwargs", type=_json_arg, default={}, help='JSON; "{i}" in strings becomes the job index'
    )
    load.add_argument(
        "--reload-every", type=float, help="send SIGHUP every S seconds (needs --pidfile)"
    )
    load.add_argument("--pidfile", help="the master's pidfile (for --reload-every and CPU time)")
    load.add_argument("--experiment", default="load", help="ExperimentRecord experiment name")
    load.add_argument("--variant", default="default", help="ExperimentRecord variant name")
    load.add_argument("--out", type=Path, help="results folder (default: <repo>/results)")


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
    parser.add_argument("--host", default="127.0.0.1", help="master host for client commands")
    parser.add_argument("--port", type=int, default=7340, help="master port for client commands")
    parser.add_argument(
        "--trace", action="store_true", help="print every KP/1 frame (hex + JSON) to stderr"
    )
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"))
    parser.add_argument("--log-format", choices=("kv", "json"))
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
    # WHY: listed here so `kiln --help` shows it; main() hands its arguments straight to
    # kiln.forkcheck.main, which has its own parser (`kiln forkcheck --help`).
    subcommands.add_parser(
        "forkcheck",
        help="find code that silently depends on fork (spawn/forkserver breakage)",
        add_help=False,
    )
    _add_server_commands(subcommands)
    _add_client_commands(subcommands)
    _add_bench_commands(subcommands)
    mem = subcommands.add_parser(
        "mem",
        help="show RSS/PSS/USS/shared memory of processes (K3)",
        description="Read /proc/<pid>/smaps_rollup and print one row per process.",
        epilog=memtable.HELP_COLUMNS,
        # WHY: keeps the column list's line breaks in --help.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mem.add_argument("pids", nargs="*", type=int, metavar="PID", help="process ids to show")
    mem.add_argument(
        "--children-of", type=int, metavar="PID", help="a master pid: show it and its workers"
    )
    mem.add_argument("--json", action="store_true", help="print bytes as JSON")
    mem.add_argument(
        "--watch", type=float, metavar="SECONDS", help="repeat every SECONDS until Ctrl-C"
    )
    return parser


def _cmd_mem(args: argparse.Namespace) -> int:
    """`kiln mem`: print the memory table once, or every --watch seconds.

    Why:
        The K3 lab's question ("how much is really shared?") answered from the shell.

    Args:
        args: parsed arguments.

    Returns:
        0, or 2 if no process was given.
    """
    if not args.pids and args.children_of is None:
        print("kiln mem: give PIDs or --children-of PID", file=sys.stderr)
        return EXIT_USAGE
    while True:
        rows = memtable.collect(args.pids, args.children_of)
        if args.json:
            _out(json.dumps(memtable.to_json(rows), indent=2))
        else:
            _out(memtable.render_text(rows))
        if not args.watch:
            return 0
        try:
            time.sleep(args.watch)
        except KeyboardInterrupt:
            return 0
        _out("")


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
        _err(f"kiln doctor: {error}")
        return EXIT_USAGE
    results = doctor.run_checks(doctor.DoctorContext.real(), milestone=milestone)
    _out(doctor.render_json(results) if args.json else doctor.render_table(results))
    return doctor.exit_code(results)


def _serve_overrides(args: argparse.Namespace) -> dict[str, object]:
    """Turn `kiln serve` flags into "section.key" config overrides (highest precedence).

    Why:
        CLI flags are the highest-precedence config source (spec/05 §1); only flags the user typed
        may override anything.

    Args:
        args: the parsed arguments.

    Returns:
        Only the settings the user actually gave.
    """
    pairs = {
        "app.spec": args.app_spec,
        "master.workers": args.workers,
        "master.bind": args.bind,
        "master.pidfile": args.pidfile,
        "start.method": args.start_method,
        "worker.executor": args.executor,
        "log.level": args.log_level,
        "log.format": args.log_format,
    }
    overrides: dict[str, object] = {key: value for key, value in pairs.items() if value is not None}
    if args.labs:
        overrides["labs.enabled"] = True
    if args.debug:
        overrides["debug.enabled"] = True
    return overrides


def _cmd_serve(args: argparse.Namespace) -> int:
    """Run the master until it shuts down.

    Why:
        Loads and validates the config before anything starts, so a typo fails with exit code 2
        instead of a half-started server.

    Args:
        args: the parsed arguments.

    Returns:
        The master's exit code (spec/04 §5.6), or 2 for configuration errors.
    """
    try:
        config = load_config(args.config, os.environ, _serve_overrides(args))
    except ConfigError as error:
        _err(str(error))
        return EXIT_USAGE
    if not config.app.spec:
        _err("kiln serve: no app given. Example: kiln serve examples.demo:app")
        return EXIT_USAGE
    # WHY: like gunicorn, make the current folder importable, so "examples.demo:app" works
    # when you run kiln from the project folder (the kiln command's own folder is on sys.path,
    # not your current one).
    if str(Path.cwd()) not in sys.path:
        sys.path.insert(0, str(Path.cwd()))
    config_path = (
        args.config if args.config else (Path("kiln.toml") if Path("kiln.toml").exists() else None)
    )
    return Arbiter(config, config_path).run()


def _client(args: argparse.Namespace) -> KilnClient:
    """Connect a client using the global --host/--port/--trace flags.

    Args:
        args: the parsed arguments.

    Returns:
        A connected client.
    """
    client = KilnClient(args.host, args.port, trace=args.trace)
    client.connect(retry_for_s=10.0)
    return client


def _cmd_client(args: argparse.Namespace) -> int:
    """Run one of submit / status / wait / cancel / stats and print the result as JSON.

    Why:
        The five client commands share connecting, error handling and JSON printing; one function
        avoids five copies.

    Args:
        args: the parsed arguments.

    Returns:
        0 on success, 1 if the server returned an error.
    """
    try:
        client = _client(args)
        if args.command == "submit":
            job_id = client.submit(
                args.task, args.args, args.kwargs, args.timeout, args.max_attempts
            )
            result: object = (
                client.wait(job_id, timeout_s=300.0)["job"] if args.wait else {"job_id": job_id}
            )
        elif args.command == "status":
            result = client.status(args.job_id)
        elif args.command == "wait":
            result = client.wait(args.job_id, timeout_s=args.timeout)
        elif args.command == "cancel":
            result = client.cancel(args.job_id)
        else:
            result = client.stats()
        client.close()
    except KilnError as error:
        _err(f"kiln {args.command}: {error}")
        return 1
    _out(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _read_pidfile(path: str) -> int:
    """Read the master's pid.

    Args:
        path: the pidfile.

    Returns:
        The pid.

    Raises:
        KilnError: missing or unreadable pidfile.
    """
    try:
        return int(Path(path).read_text(encoding="ascii").strip())
    except (OSError, ValueError) as error:
        raise KilnError(f"cannot read pid from {path}: {error}") from error


def _cmd_signal(args: argparse.Namespace) -> int:
    """`reload`, `stop` and `scale`: send the master the matching signal (spec/04 §3).

    Why:
        Signals are how you control a running master (spec/04 §3); the pidfile tells us which
        process to signal.

    Args:
        args: the parsed arguments.

    Returns:
        0 on success, 1 if the master can't be found.
    """
    try:
        pid = _read_pidfile(args.pidfile)
        if args.command == "reload":
            os.kill(pid, signal.SIGHUP)
        elif args.command == "stop" and args.fast:
            os.kill(pid, signal.SIGINT)
            time.sleep(0.2)  # WHY: two separate signals: the second one means "fast"
            os.kill(pid, signal.SIGINT)
        elif args.command == "stop":
            os.kill(pid, signal.SIGTERM)
        else:
            sig = signal.SIGTTIN if args.delta > 0 else signal.SIGTTOU
            for _ in range(abs(args.delta)):
                os.kill(pid, sig)
                time.sleep(0.05)  # WHY: signals don't queue; a pause keeps each one distinct
    except (KilnError, ProcessLookupError) as error:
        _err(f"kiln {args.command}: {error}")
        return 1
    return 0


def _cmd_bench(args: argparse.Namespace) -> int:
    """`bench load`: run a load test and print the record's headline numbers.

    Why:
        Turns command-line options into LoadOptions and prints the headline numbers, keeping
        argparse out of bench/load.py.

    Args:
        args: the parsed arguments.

    Returns:
        0 on success, 2 on misuse.
    """
    if args.bench_command != "load":
        _err("kiln bench: choose a tool, e.g. `kiln bench load --help`")
        return EXIT_USAGE
    options = LoadOptions(
        jobs=args.jobs,
        concurrency=args.concurrency if args.rate is None else (args.concurrency or 64),
        rate=args.rate,
        task=args.task,
        args=args.args,
        kwargs=args.kwargs,
        host=args.host,
        port=args.port,
        reload_every_s=args.reload_every,
        pidfile=args.pidfile,
        experiment=args.experiment,
        variant=args.variant,
        results_dir=args.out,
    )
    record, path = run_load(options)
    metrics = record["metrics"]
    assert isinstance(metrics, dict)  # INVARIANT: records always have a metrics dict
    summary = {name: metric.get("value", metric.get("summary")) for name, metric in metrics.items()}
    _out(json.dumps({"record": str(path), "metrics": summary}, indent=2))
    return 0


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
    arguments = sys.argv[1:] if argv is None else argv
    if arguments[:1] == ["forkcheck"]:
        # WHY: forkcheck is a self-contained tool with its own options and exit codes
        # (K2 spec §5.8); passing everything after the word "forkcheck" keeps them intact.
        return forkcheck.main(arguments[1:])
    parser = build_parser()
    # WHY: parse_args() prints an error and exits with code 2 by itself on bad usage.
    args = parser.parse_args(arguments)
    handlers = {
        "doctor": _cmd_doctor,
        "serve": _cmd_serve,
        "submit": _cmd_client,
        "status": _cmd_client,
        "wait": _cmd_client,
        "cancel": _cmd_client,
        "stats": _cmd_client,
        "reload": _cmd_signal,
        "stop": _cmd_signal,
        "scale": _cmd_signal,
        "bench": _cmd_bench,
        "mem": _cmd_mem,
    }
    handler = handlers.get(args.command)
    if handler is None:
        # WHY: no subcommand given. Show help instead of doing nothing silently.
        parser.print_help()
        return EXIT_USAGE
    return handler(args)
