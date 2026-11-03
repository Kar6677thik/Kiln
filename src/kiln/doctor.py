"""`kiln doctor`: inspect this machine and explain, in plain words, what Kiln still needs.

WHY THIS MODULE EXISTS
    Kiln's labs depend on things outside Python: Linux features (/proc, cgroups, ptrace),
    several Python interpreters, a C compiler, Rust... When one is missing, the error usually
    shows up much later and far away ("Operation not permitted" deep inside a K6 lab). A
    doctor-style command checks everything up front and prints a fix for each problem.
    The exact list of checks D1–D19 is defined in docs/spec/07-environment.md §12.

HOW IT FITS
    kiln.cli `doctor` ──▶ run_checks(DoctorContext.real(), milestone) ──▶ list[CheckResult]
                                                                      ──▶ render_table / render_json
    Every check reads the machine only through a DoctorContext. Tests build a fake context
    (a pretend /proc folder, a pretend command runner), so each check can be tested on any
    machine: see tests/unit/test_doctor.py.

READ FIRST
    docs/spec/07-environment.md §12 (the check table), docs/concepts/files-and-file-descriptors.md
    (why /proc looks like files), docs/python/dataclasses.md, docs/python/enum.md

MILESTONE
    Introduced in K0.
"""

import importlib.util
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import sysconfig

# PY: "from X import Y" pulls specific names out of a module.
# See docs/python/modules-and-imports.md.
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

# WHY: one timeout for every external command (K0 spec §5.6), so doctor can never hang.
COMMAND_TIMEOUT_S = 10.0
# WHY: the tools the primers and labs ask you to run (docs/spec/07-environment.md §2).
LAB_TOOLS = ("gdb", "strace", "rsync", "taskset")
# WHY: Kiln's two default ports: KP/1 (master) and HTTP (gateway). See docs/spec/05 §2.
KILN_PORTS = (7340, 7341)
MATRIX_INTERPRETERS = ("3.13", "3.14", "3.15", "3.15t")
K7_INTERPRETERS = ("3.14.4", "3.14.5")
GIB = 1024**3


# PY: StrEnum (3.11+) is an Enum whose members are also plain strings, so they print and
# compare as text ("pass"). See docs/python/enum.md.
class Status(StrEnum):
    """The outcome of one check.

    Why:
        A fixed set of named values is safer than loose strings: a typo like "pas" becomes an
        AttributeError instead of a silently wrong report.
    """

    PASS = "pass"  # noqa: S105  # WHY: "PASS" means "check passed", not a password
    WARN = "warn"
    FAIL = "fail"
    INFO = "info"


# WHY: the symbols the spec uses in its tables (docs/spec/07-environment.md §12).
SYMBOLS = {Status.PASS: "✅", Status.WARN: "⚠️ ", Status.FAIL: "❌", Status.INFO: "ℹ️ "}


# PY: @dataclass writes __init__, __repr__ and __eq__ for us from the annotated fields below;
# frozen=True makes instances read-only after creation. See docs/python/dataclasses.md.
@dataclass(frozen=True)
class CheckResult:
    """The result of one doctor check, ready to print.

    Why:
        A small immutable record keeps "what we found" separate from "how we display it"
        (table or JSON), and makes test assertions simple.
    """

    id: str
    title: str
    status: Status
    detail: str
    fix: str = ""
    # WHY: the earliest milestone that needs this. `--milestone Kn` turns warnings whose
    # needed_by ≤ Kn into failures (K0 spec §5.6). None means "never escalate".
    needed_by: str | None = None


@dataclass(frozen=True)
class RunResult:
    """What happened when doctor ran an external command.

    Why:
        Checks need three facts about a command (exit code, output, did it time out), and a
        fake runner in the tests must be able to produce the same shape.
    """

    returncode: int
    stdout: str
    timed_out: bool = False


# PY: a type alias: "Runner" is a shorter name for "a function taking a list of strings and
# returning a RunResult". See docs/python/type-hints.md.
Runner = Callable[[list[str]], RunResult]


def run_command(argv: list[str]) -> RunResult:
    """Run an external command with a timeout and capture its output.

    Why:
        Doctor must survive missing programs and hung programs alike. A missing program is
        reported as exit code 127 (the shell convention for "command not found").

    Args:
        argv: the program and its arguments, as a list (never a single shell string).

    Returns:
        The exit code and standard output; timed_out=True if it ran longer than 10 s.
    """
    try:
        # CONCEPT(what-is-a-process): subprocess.run starts a child process and waits for it.
        # WHY: argv is a list built by us, never user text, so there is no injection risk.
        completed = subprocess.run(  # noqa: S603
            argv, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S, check=False
        )
    except FileNotFoundError:
        return RunResult(returncode=127, stdout="")
    except subprocess.TimeoutExpired:
        return RunResult(returncode=-1, stdout="", timed_out=True)
    return RunResult(returncode=completed.returncode, stdout=completed.stdout.strip())


def _port_is_free(port: int) -> bool:
    """Try to bind 127.0.0.1:<port> and report whether it worked.

    Why:
        Binding is the only reliable way to know a port is free: another program may be
        listening on it right now (check D18).

    Args:
        port: the TCP port number to test.

    Returns:
        True if nothing else is using the port.
    """
    # PY: "with" closes the socket automatically at the end of the block.
    # See docs/python/context-managers.md.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _module_exists(name: str) -> bool:
    """Report whether a module can be imported, without importing it.

    Why:
        Check D19 asks "does this interpreter have the 3.15 profiler?" Finding the module is
        enough to answer that, and actually importing it would start profiler machinery.

    Args:
        name: a dotted module name such as "profiling.sampling".

    Returns:
        True if Python can find the module.
    """
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        # WHY: find_spec("a.b") raises when the parent package "a" itself is missing.
        return False


def is_free_threaded_build() -> bool:
    """Report whether this interpreter is a free-threaded ("t") build.

    Why:
        Several reports (`kiln --version`, ExperimentRecords) must say which build produced
        them. On a free-threaded build, the config variable Py_GIL_DISABLED is 1.

    Returns:
        True on builds such as 3.15t, False on normal (GIL) builds.
    """
    return bool(sysconfig.get_config_var("Py_GIL_DISABLED"))


@dataclass
class DoctorContext:
    """Everything a check is allowed to look at, gathered in one place.

    Why:
        If checks read /proc or run programs directly, they can only be tested on a machine
        that has exactly the right setup. Passing all of that in as one object lets the tests
        pass a fake folder tree and a fake command runner instead (K0 spec §5.6).
    """

    proc_root: Path
    sys_root: Path
    repo_root: Path
    run: Runner
    which: Callable[[str], str | None]
    env: Mapping[str, str]
    platform_name: str
    kernel_release: str
    python_version: tuple[int, int]
    sys_prefix: str
    cpu_count: int
    port_free: Callable[[int], bool]
    module_exists: Callable[[str], bool]
    has_remote_exec: bool
    # PY: field(default_factory=...) gives each instance its own fresh value.
    notes: list[str] = field(default_factory=list)

    # PY: @classmethod makes a method that is called on the class itself (DoctorContext.real())
    # and receives the class as "cls". A common way to write alternative constructors.
    @classmethod
    def real(cls) -> "DoctorContext":
        """Build a context that looks at the real machine.

        Why:
            The production path: real /proc, real subprocesses, the real interpreter.

        Returns:
            A DoctorContext wired to the live system.
        """
        return cls(
            proc_root=Path("/proc"),
            sys_root=Path("/sys"),
            # WHY: this file is src/kiln/doctor.py, so the repo is three folders up.
            repo_root=Path(__file__).resolve().parents[2],
            run=run_command,
            which=shutil.which,
            env=os.environ,
            platform_name=sys.platform,
            kernel_release=platform.release(),
            python_version=(sys.version_info.major, sys.version_info.minor),
            sys_prefix=sys.prefix,
            cpu_count=os.cpu_count() or 1,
            port_free=_port_is_free,
            module_exists=_module_exists,
            has_remote_exec=hasattr(sys, "remote_exec"),
        )


def _uv(ctx: DoctorContext) -> str:
    """Find the `uv` program, even when it isn't on PATH.

    Why:
        uv installs itself into ~/.local/bin, which isn't always on PATH in a fresh shell.

    Args:
        ctx: the doctor context (for PATH lookup and $HOME).

    Returns:
        A path or command name for uv.
    """
    found = ctx.which("uv")
    if found:
        return found
    return str(Path(ctx.env.get("HOME", "~")) / ".local" / "bin" / "uv")


def _read(path: Path) -> str | None:
    """Read a small text file, returning None if it doesn't exist or can't be read.

    Why:
        Many /proc and /sys files are missing on some kernels or unreadable without
        privileges. A check should report that, never crash on it.

    Args:
        path: the file to read.

    Returns:
        The file's text, or None.
    """
    # CONCEPT(files-and-file-descriptors): /proc files aren't on any disk; the kernel
    # generates their text at the moment you read them.
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# --- The checks (D1–D19, docs/spec/07-environment.md §12). One function per row. ---------------


def check_d1(ctx: DoctorContext) -> CheckResult:
    """D1: Kiln must run on Linux.

    Why:
        fork(), /proc, Unix signals and cgroups exist only on Linux-like systems (ADR-003).

    Args:
        ctx: the doctor context.

    Returns:
        PASS on Linux, FAIL elsewhere.
    """
    if ctx.platform_name == "linux":
        return CheckResult("D1", "Running on Linux", Status.PASS, "sys.platform = linux")
    return CheckResult(
        "D1",
        "Running on Linux",
        Status.FAIL,
        f"sys.platform = {ctx.platform_name}",
        fix="run inside WSL: wsl -d Debian (see docs/concepts/terminal-and-wsl.md)",
        needed_by="K0",
    )


def check_d2(ctx: DoctorContext) -> CheckResult:
    """D2: note whether we are inside WSL2.

    Why:
        Reports must mention WSL because of its quirks (9P file access, shared RAM).

    Args:
        ctx: the doctor context.

    Returns:
        Always INFO.
    """
    inside = "microsoft" in ctx.kernel_release.lower()
    detail = f"WSL2 kernel {ctx.kernel_release}" if inside else f"kernel {ctx.kernel_release}"
    return CheckResult("D2", "Inside WSL2", Status.INFO, detail)


def check_d3(ctx: DoctorContext) -> CheckResult:
    """D3: the running Python is at least 3.13, Kiln's syntax floor.

    Why:
        Kiln's code uses syntax that older versions can't parse (ADR-004).

    Args:
        ctx: the doctor context.

    Returns:
        PASS for 3.13+, FAIL otherwise.
    """
    major, minor = ctx.python_version
    detail = f"running Python {major}.{minor}"
    if (major, minor) >= (3, 13):
        return CheckResult("D3", "Python version ≥ 3.13", Status.PASS, detail)
    return CheckResult(
        "D3", "Python version ≥ 3.13", Status.FAIL, detail, "uv python install 3.15", "K0"
    )


def _find_interpreters(ctx: DoctorContext, versions: tuple[str, ...]) -> list[str]:
    """Return the versions that `uv python find` can NOT locate.

    Why:
        Shared by D4 (test matrix) and D5 (K7's pinned versions).

    Args:
        ctx: the doctor context.
        versions: version requests such as "3.15t" or "3.14.4".

    Returns:
        The missing versions, in the order given.
    """
    missing = []
    for version in versions:
        result = ctx.run([_uv(ctx), "python", "find", version])
        if result.returncode != 0:
            missing.append(version)
    return missing


def check_d4(ctx: DoctorContext) -> CheckResult:
    """D4: every test-matrix interpreter is installed.

    Why:
        `dev.sh test-matrix` runs the tests on 3.13, 3.14, 3.15 and 3.15t (ADR-004).

    Args:
        ctx: the doctor context.

    Returns:
        PASS if all are found, WARN listing the missing ones.
    """
    missing = _find_interpreters(ctx, MATRIX_INTERPRETERS)
    title = "Test-matrix interpreters installed"
    if not missing:
        return CheckResult("D4", title, Status.PASS, ", ".join(MATRIX_INTERPRETERS))
    return CheckResult(
        "D4",
        title,
        Status.WARN,
        "missing: " + ", ".join(missing),
        fix="uv python install " + " ".join(missing),
        needed_by="K0",
    )


def check_d5(ctx: DoctorContext) -> CheckResult:
    """D5: the two pinned interpreters for the K7 GC-regression lab are installed.

    Why:
        K7 compares 3.14.4 (incremental GC) with 3.14.5 (the revert).

    Args:
        ctx: the doctor context.

    Returns:
        PASS if both are found, WARN (needed by K7) otherwise.
    """
    missing = _find_interpreters(ctx, K7_INTERPRETERS)
    title = "K7 interpreters (3.14.4, 3.14.5)"
    if not missing:
        return CheckResult("D5", title, Status.PASS, "both installed")
    return CheckResult(
        "D5",
        title,
        Status.WARN,
        "missing: " + ", ".join(missing),
        fix="uv python install " + " ".join(missing) + " (or see K7's verification gates)",
        needed_by="K7",
    )


def check_d6(ctx: DoctorContext) -> CheckResult:
    """D6: the free-threaded 3.15t interpreter really runs without the GIL.

    Why:
        K4's free-threading experiments are meaningless if the GIL is quietly on.

    Args:
        ctx: the doctor context.

    Returns:
        PASS if 3.15t reports the GIL disabled, WARN (needed by K4) otherwise.
    """
    title = "Free-threaded 3.15t works"
    found = ctx.run([_uv(ctx), "python", "find", "3.15t"])
    if found.returncode != 0:
        return CheckResult(
            "D6", title, Status.WARN, "3.15t not installed", "uv python install 3.15t", "K4"
        )
    probe = ctx.run([found.stdout, "-c", "import sys; print(sys._is_gil_enabled())"])
    if probe.stdout == "False":
        return CheckResult("D6", title, Status.PASS, "sys._is_gil_enabled() → False")
    return CheckResult(
        "D6",
        title,
        Status.WARN,
        f"sys._is_gil_enabled() → {probe.stdout or 'no output'}",
        fix="reinstall: uv python install --reinstall 3.15t",
        needed_by="K4",
    )


def check_d7(ctx: DoctorContext) -> CheckResult:
    """D7: the virtual environment lives on WSL's fast ext4 disk, not on /mnt/c.

    Why:
        /mnt/c goes through the slow 9P protocol; imports from a venv there are 5–20× slower
        (ADR-003, docs/spec/07-environment.md §4).

    Args:
        ctx: the doctor context.

    Returns:
        PASS if sys.prefix is outside /mnt/, WARN otherwise.
    """
    title = "Virtual environment on ext4"
    if ctx.sys_prefix.startswith("/mnt/"):
        return CheckResult(
            "D7", title, Status.WARN, f"venv at {ctx.sys_prefix}", "use scripts/dev.sh", "K0"
        )
    return CheckResult("D7", title, Status.PASS, f"venv at {ctx.sys_prefix}")


def check_d8(ctx: DoctorContext) -> CheckResult:
    """D8: report whether the source code lives on /mnt (9P).

    Why:
        Timing labs must run from the ext4 snapshot (`dev.sh bench-snapshot`), so you need
        to know where you are.

    Args:
        ctx: the doctor context.

    Returns:
        Always INFO.
    """
    root = str(ctx.repo_root)
    if root.startswith("/mnt/"):
        detail = f"{root} is on 9P; timing labs run from ~/kiln-bench (dev.sh bench-snapshot)"
    else:
        detail = f"{root} is on a Linux filesystem"
    return CheckResult("D8", "Source location", Status.INFO, detail)


def check_d9(ctx: DoctorContext) -> CheckResult:
    """D9: a C compiler (`cc`) is installed.

    Why:
        K4 builds a small C extension and K8's Rust linker calls `cc`.

    Args:
        ctx: the doctor context.

    Returns:
        PASS if `cc` is on PATH, WARN (needed by K4) otherwise.
    """
    path = ctx.which("cc")
    if path:
        return CheckResult("D9", "C compiler", Status.PASS, path)
    return CheckResult(
        "D9", "C compiler", Status.WARN, "cc not found", "sudo apt install build-essential", "K4"
    )


def check_d10(ctx: DoctorContext) -> CheckResult:
    """D10: the Rust toolchain (cargo) is installed.

    Why:
        K8 builds a PyO3 extension in Rust.

    Args:
        ctx: the doctor context.

    Returns:
        PASS if `cargo --version` works, WARN (needed by K8) otherwise.
    """
    cargo = ctx.which("cargo") or str(Path(ctx.env.get("HOME", "~")) / ".cargo" / "bin" / "cargo")
    result = ctx.run([cargo, "--version"])
    if result.returncode == 0:
        return CheckResult("D10", "Rust toolchain", Status.PASS, result.stdout)
    return CheckResult(
        "D10",
        "Rust toolchain",
        Status.WARN,
        "cargo not found",
        fix="see docs/spec/07-environment.md §9 (rustup)",
        needed_by="K8",
    )


def check_d11(ctx: DoctorContext) -> CheckResult:
    """D11: whether debuggers and profilers may attach to other processes (ptrace_scope).

    Why:
        `pdb -p`, `asyncio ps` and the Tachyon profiler (K5/K6) need to attach to a running
        process. ptrace_scope = 1 only lets a process trace its own descendants.

    Args:
        ctx: the doctor context.

    Returns:
        PASS when attaching is allowed (0), WARN (needed by K5) when restricted.
    """
    text = _read(ctx.proc_root / "sys" / "kernel" / "yama" / "ptrace_scope")
    title = "ptrace_scope (attach tools)"
    if text is None:
        return CheckResult("D11", title, Status.INFO, "Yama not present: attach not restricted")
    value = text.strip()
    if value == "0":
        return CheckResult("D11", title, Status.PASS, "ptrace_scope = 0")
    return CheckResult(
        "D11",
        title,
        Status.WARN,
        f"ptrace_scope = {value}: attaching to non-child processes is blocked",
        fix="for a lab session: sudo sysctl kernel.yama.ptrace_scope=0 (docs/spec/07 §5 option B)",
        needed_by="K5",
    )


def check_d12(ctx: DoctorContext) -> CheckResult:
    """D12: the machine uses cgroup v2 (one unified hierarchy).

    Why:
        The K1 OOM lab limits memory with a cgroup v2 `memory.max` file.

    Args:
        ctx: the doctor context.

    Returns:
        PASS if /sys/fs/cgroup/cgroup.controllers exists, WARN otherwise.
    """
    # WHY: cgroup.controllers exists only at the root of a cgroup v2 hierarchy, so its
    # presence is a simple and reliable "is this v2?" test.
    controllers = _read(ctx.sys_root / "fs" / "cgroup" / "cgroup.controllers")
    if controllers is not None:
        detail = "controllers: " + (controllers.strip() or "(none listed)")
        return CheckResult("D12", "cgroup v2", Status.PASS, detail)
    return CheckResult(
        "D12", "cgroup v2", Status.WARN, "no cgroup v2 hierarchy", "see docs/spec/07 §6", "K1"
    )


def check_d13(ctx: DoctorContext) -> CheckResult:
    """D13: we can create a memory-limited cgroup for the OOM lab.

    Why:
        K1 must trigger a *real* kernel OOM kill safely, inside a 512 MiB cgroup (ADR-023).

    Args:
        ctx: the doctor context.

    Returns:
        PASS if systemd-run (preferred) or passwordless sudo works, WARN (needed by K1) otherwise.
    """
    title = "Memory-limited cgroup possible"
    systemd = ctx.run(["systemd-run", "--user", "--scope", "--quiet", "true"])
    if systemd.returncode == 0:
        return CheckResult("D13", title, Status.PASS, "systemd-run --user --scope works")
    sudo = ctx.run(["sudo", "-n", "true"])
    if sudo.returncode == 0:
        return CheckResult("D13", title, Status.PASS, "passwordless sudo available (manual cgroup)")
    return CheckResult(
        "D13",
        title,
        Status.WARN,
        "neither systemd-run --user nor passwordless sudo works",
        fix="enable systemd in /etc/wsl.conf, or use the sudo steps (docs/spec/07 §6)",
        needed_by="K1",
    )


def check_d14(ctx: DoctorContext) -> CheckResult:
    """D14: /proc/self/smaps_rollup can be read.

    Why:
        K3 measures shared vs private memory (PSS/USS) from this file.

    Args:
        ctx: the doctor context.

    Returns:
        PASS if the file can be read and has a Pss line, FAIL otherwise.
    """
    text = _read(ctx.proc_root / "self" / "smaps_rollup")
    if text is not None and "Pss:" in text:
        return CheckResult("D14", "smaps_rollup readable", Status.PASS, "Pss/Rss lines present")
    return CheckResult(
        "D14",
        "smaps_rollup readable",
        Status.FAIL,
        "cannot read /proc/self/smaps_rollup",
        fix="needs Linux kernel ≥ 4.14",
        needed_by="K3",
    )


def _mem_total_bytes(ctx: DoctorContext) -> int | None:
    """Read the machine's total RAM from /proc/meminfo.

    Why:
        Experiments are sized to fit the memory WSL can see (ADR-023).

    Args:
        ctx: the doctor context.

    Returns:
        Total memory in bytes, or None if /proc/meminfo is unreadable.
    """
    text = _read(ctx.proc_root / "meminfo")
    if text is None:
        return None
    for line in text.splitlines():
        # WHY: the line looks like "MemTotal:        7864320 kB"; kB here means KiB (×1024).
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024
    return None


def check_d15(ctx: DoctorContext) -> CheckResult:
    """D15: report CPU count and RAM.

    Why:
        Experiment sizes (workers, preload size) depend on them (ADR-023).

    Args:
        ctx: the doctor context.

    Returns:
        INFO, or WARN (needed by K3) when RAM is under 4 GiB.
    """
    total = _mem_total_bytes(ctx)
    title = "CPUs and RAM"
    if total is None:
        return CheckResult("D15", title, Status.INFO, f"{ctx.cpu_count} CPUs, RAM unknown")
    detail = f"{ctx.cpu_count} CPUs, {total / GIB:.1f} GiB RAM"
    if total < 4 * GIB:
        fix = "give WSL more memory in .wslconfig (docs/spec/07 §8)"
        return CheckResult("D15", title, Status.WARN, detail, fix, "K3")
    return CheckResult("D15", title, Status.INFO, detail)


def check_d16(ctx: DoctorContext) -> CheckResult:
    """D16: shell scripts have Unix (LF) line endings.

    Why:
        A script saved with Windows (CRLF) line endings fails with a confusing
        "command not found" error caused by an invisible carriage-return character.

    Args:
        ctx: the doctor context.

    Returns:
        PASS if no scripts/*.sh contains a carriage return, FAIL naming the bad files otherwise.
    """
    # PY: a list comprehension builds a list in one expression. See docs/python/comprehensions.md.
    bad = [
        path.name
        for path in sorted((ctx.repo_root / "scripts").glob("*.sh"))
        if b"\r" in path.read_bytes()
    ]
    if not bad:
        return CheckResult("D16", "Line endings (LF)", Status.PASS, "scripts/*.sh use LF")
    return CheckResult(
        "D16",
        "Line endings (LF)",
        Status.FAIL,
        "CRLF found in: " + ", ".join(bad),
        fix="git add --renormalize . (and check .gitattributes)",
        needed_by="K0",
    )


def check_d17(ctx: DoctorContext) -> CheckResult:
    """D17: the command-line tools used by primers and labs are installed.

    Why:
        Primers ask you to run strace or gdb; the bench snapshot needs rsync.

    Args:
        ctx: the doctor context.

    Returns:
        PASS if all are present, WARN (needed by K1) listing the missing ones.
    """
    missing = [tool for tool in LAB_TOOLS if ctx.which(tool) is None]
    if not missing:
        return CheckResult("D17", "Lab tools", Status.PASS, ", ".join(LAB_TOOLS))
    return CheckResult(
        "D17",
        "Lab tools",
        Status.WARN,
        "missing: " + ", ".join(missing),
        fix="sudo apt install " + " ".join(missing),
        needed_by="K1",
    )


def check_d18(ctx: DoctorContext) -> CheckResult:
    """D18: Kiln's default ports (7340, 7341) are free.

    Why:
        `kiln serve` binds them; if another program holds them, the server can't start.

    Args:
        ctx: the doctor context.

    Returns:
        PASS if both are free, WARN (needed by K1) naming the busy ones.
    """
    busy = [str(port) for port in KILN_PORTS if not ctx.port_free(port)]
    if not busy:
        return CheckResult("D18", "Ports 7340/7341 free", Status.PASS, "both free")
    return CheckResult(
        "D18",
        "Ports 7340/7341 free",
        Status.WARN,
        "in use: " + ", ".join(busy),
        fix="stop the other program (find it with: ss -ltnp)",
        needed_by="K1",
    )


def check_d19(ctx: DoctorContext) -> CheckResult:
    """D19: this interpreter has the debugging tools K5/K6 use.

    Why:
        Tachyon (`profiling.sampling`) arrived in 3.15 and remote attach (`sys.remote_exec`,
        PEP 768) in 3.14.

    Args:
        ctx: the doctor context.

    Returns:
        PASS if both are available, WARN (needed by K5) otherwise.
    """
    missing = []
    if not ctx.module_exists("profiling.sampling"):
        missing.append("profiling.sampling (3.15+)")
    if not ctx.has_remote_exec:
        missing.append("sys.remote_exec (3.14+)")
    title = "Debug tools on this interpreter"
    if not missing:
        return CheckResult("D19", title, Status.PASS, "profiling.sampling, sys.remote_exec")
    return CheckResult(
        "D19", title, Status.WARN, "missing: " + ", ".join(missing), "use Python 3.15", "K5"
    )


# WHY: an explicit, ordered list of every check: easy to read, easy to test one by one.
ALL_CHECKS: list[Callable[[DoctorContext], CheckResult]] = [
    check_d1,
    check_d2,
    check_d3,
    check_d4,
    check_d5,
    check_d6,
    check_d7,
    check_d8,
    check_d9,
    check_d10,
    check_d11,
    check_d12,
    check_d13,
    check_d14,
    check_d15,
    check_d16,
    check_d17,
    check_d18,
    check_d19,
]


def parse_milestone(text: str) -> int:
    """Turn "K4" (or "k4", or "4") into the number 4.

    Why:
        `--milestone` compares milestones by number, and people type them in several ways.

    Args:
        text: the user's milestone text.

    Returns:
        The milestone number, 0–10.

    Raises:
        ValueError: if the text isn't a milestone between K0 and K10.

    Example:
        >>> parse_milestone("K4")
        4
    """
    digits = text.strip().upper().removeprefix("K")
    if not digits.isdigit() or not 0 <= int(digits) <= 10:
        # PY: "raise" signals an error to the caller. See docs/python/exceptions.md.
        raise ValueError(f"--milestone must be K0..K10 (got {text!r})")
    return int(digits)


def escalate(result: CheckResult, milestone: int | None) -> CheckResult:
    """Turn a warning into a failure if the given milestone needs it.

    Why:
        Before starting a milestone, `kiln doctor --milestone Kn` should fail on anything
        that milestone (or an earlier one) needs (K0 spec §5.6).

    Args:
        result: one check's result.
        milestone: the milestone number from --milestone, or None.

    Returns:
        The same result, or a copy with status FAIL.
    """
    if milestone is None or result.status != Status.WARN or result.needed_by is None:
        return result
    if parse_milestone(result.needed_by) <= milestone:
        # PY: dataclasses.replace() copies a frozen dataclass with some fields changed.
        return replace(result, status=Status.FAIL)
    return result


def run_checks(ctx: DoctorContext, milestone: int | None = None) -> list[CheckResult]:
    """Run every check in order and apply `--milestone` escalation.

    Why:
        One place that defines "a doctor run", used by the CLI and by tests.

    Args:
        ctx: the doctor context (real or fake).
        milestone: the --milestone number, or None.

    Returns:
        One CheckResult per check, D1 first.
    """
    return [escalate(check(ctx), milestone) for check in ALL_CHECKS]


def exit_code(results: list[CheckResult]) -> int:
    """Return 1 if any check failed, otherwise 0.

    Why:
        Scripts (and `dev.sh doctor`) use the exit code to decide whether to continue.

    Args:
        results: the doctor results.

    Returns:
        0 or 1.
    """
    return 1 if any(result.status == Status.FAIL for result in results) else 0


def render_table(results: list[CheckResult]) -> str:
    """Format the results as a human-readable table with fix hints.

    Why:
        The default output is for people: one line per check, and a "fix:" line under every
        warning or failure so you know what to do next.

    Args:
        results: the doctor results.

    Returns:
        The table as one multi-line string.
    """
    lines = []
    for result in results:
        lines.append(f"{SYMBOLS[result.status]} {result.id:<4} {result.title:<34} {result.detail}")
        if result.status in (Status.WARN, Status.FAIL) and result.fix:
            needed = f" (needed by {result.needed_by})" if result.needed_by else ""
            lines.append(f"        fix{needed}: {result.fix}")
    failed = sum(1 for result in results if result.status == Status.FAIL)
    warned = sum(1 for result in results if result.status == Status.WARN)
    lines.append("")
    lines.append(f"{failed} failed, {warned} warnings, {len(results)} checks")
    return "\n".join(lines)


def render_json(results: list[CheckResult]) -> str:
    """Format the results as JSON, for scripts and for the K0 setup log.

    Why:
        Machine-readable output can be saved (results/published/K0/doctor.json) and compared.

    Args:
        results: the doctor results.

    Returns:
        A JSON array with one object per check.
    """
    rows = [
        {
            "id": result.id,
            "title": result.title,
            "status": str(result.status),
            "detail": result.detail,
            "fix": result.fix,
            "needed_by": result.needed_by,
        }
        for result in results
    ]
    return json.dumps(rows, indent=2, ensure_ascii=False)
