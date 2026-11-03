"""Tests for `kiln doctor`, run against a fake machine (a pretend /proc tree and command runner).

Every check reads the system only through a DoctorContext, so these tests can describe any
machine they like: a healthy one, one with no C compiler, one where ptrace is restricted...
"""

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from kiln import doctor
from kiln.doctor import CheckResult, DoctorContext, RunResult, Status

SMAPS_ROLLUP = "Rss:  1000 kB\nPss:  900 kB\nPss_Anon: 100 kB\n"
MEMINFO = "MemTotal:        7864320 kB\nMemFree:  100 kB\n"


@dataclass
class FakeRunner:
    """Pretends to run commands, returning canned results.

    Why:
        Doctor runs `uv python find`, `cargo --version`, `systemd-run`... Tests must not depend
        on what this machine has installed, so commands are answered from a table instead.
        Pass `FakeRunner(answers).run` wherever doctor expects a command runner.
    """

    answers: dict[str, RunResult]
    calls: list[list[str]] = field(default_factory=list)

    def run(self, argv: list[str]) -> RunResult:
        """Answer one command the way the real runner would.

        Args:
            argv: the command; the program's folder is ignored when matching.

        Returns:
            The canned result, or exit code 127 ("command not found").
        """
        self.calls.append(argv)
        text = " ".join([Path(argv[0]).name] + argv[1:])
        return self.answers.get(text, RunResult(returncode=127, stdout=""))


def healthy_answers() -> dict[str, RunResult]:
    """Return command answers for a fully set-up machine.

    Returns:
        A command → result table where every interpreter and tool is found.
    """
    answers = {
        f"uv python find {version}": RunResult(0, f"/py/{version}")
        for version in ("3.13", "3.14", "3.15", "3.15t", "3.14.4", "3.14.5")
    }
    answers["3.15t -c import sys; print(sys._is_gil_enabled())"] = RunResult(0, "False")
    answers["cargo --version"] = RunResult(0, "cargo 1.91.0")
    answers["systemd-run --user --scope --quiet true"] = RunResult(0, "")
    return answers


def make_context(tmp_path: Path, **overrides: object) -> DoctorContext:
    """Build a fake, healthy machine under tmp_path; keyword arguments change single parts.

    Args:
        tmp_path: pytest's temporary folder for this test.
        **overrides: DoctorContext fields to replace, e.g. platform_name="win32".

    Returns:
        A DoctorContext describing the fake machine.
    """
    proc = tmp_path / "proc"
    (proc / "sys" / "kernel" / "yama").mkdir(parents=True)
    (proc / "sys" / "kernel" / "yama" / "ptrace_scope").write_text("0\n")
    (proc / "self").mkdir()
    (proc / "self" / "smaps_rollup").write_text(SMAPS_ROLLUP)
    (proc / "meminfo").write_text(MEMINFO)
    sys_root = tmp_path / "sys"
    (sys_root / "fs" / "cgroup").mkdir(parents=True)
    (sys_root / "fs" / "cgroup" / "cgroup.controllers").write_text("cpu memory pids\n")
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "dev.sh").write_bytes(b"#!/usr/bin/env bash\necho ok\n")
    context = DoctorContext(
        proc_root=proc,
        sys_root=sys_root,
        repo_root=repo,
        run=FakeRunner(healthy_answers()).run,
        which=lambda name: f"/usr/bin/{name}",
        env={"HOME": "/home/learner"},
        platform_name="linux",
        kernel_release="6.6.87.2-microsoft-standard-WSL2",
        python_version=(3, 15),
        sys_prefix="/home/learner/.cache/kiln/venvs/py3.15",
        cpu_count=14,
        port_free=lambda port: True,
        module_exists=lambda name: True,
        has_remote_exec=True,
    )
    return replace(context, **overrides)  # type: ignore[arg-type]


def which_without_cc(name: str) -> str | None:
    """Pretend `shutil.which`: every program is found in /usr/bin except the C compiler.

    Args:
        name: the program to look for.

    Returns:
        A fake path, or None for "cc".
    """
    return None if name == "cc" else f"/usr/bin/{name}"


def by_id(results: list[CheckResult]) -> dict[str, CheckResult]:
    """Index results by check id for easy assertions.

    Args:
        results: doctor results.

    Returns:
        {"D1": result, ...}.
    """
    return {result.id: result for result in results}


def test_healthy_machine_has_no_failures_or_warnings(tmp_path: Path) -> None:
    """On a fully set-up fake machine, all 19 checks run and none warns or fails (AC-0.4)."""
    results = doctor.run_checks(make_context(tmp_path))
    assert [r.id for r in results] == [f"D{n}" for n in range(1, 20)]
    statuses = {r.status for r in results}
    assert Status.FAIL not in statuses
    assert Status.WARN not in statuses
    assert doctor.exit_code(results) == 0


def test_not_linux_fails_d1_with_wsl_hint(tmp_path: Path) -> None:
    """Running on Windows itself fails D1 and tells you to use WSL (ADR-003)."""
    result = doctor.check_d1(make_context(tmp_path, platform_name="win32"))
    assert result.status is Status.FAIL
    assert "wsl" in result.fix.lower()


def test_old_python_fails_d3(tmp_path: Path) -> None:
    """Python 3.12 is below Kiln's 3.13 syntax floor (ADR-004), so D3 fails."""
    assert doctor.check_d3(make_context(tmp_path, python_version=(3, 12))).status is Status.FAIL


def test_missing_interpreters_warn_with_install_command(tmp_path: Path) -> None:
    """A missing matrix interpreter makes D4 warn and prints the exact `uv python install`."""
    answers = healthy_answers()
    answers.pop("uv python find 3.15t")
    result = doctor.check_d4(make_context(tmp_path, run=FakeRunner(answers).run))
    assert result.status is Status.WARN
    assert result.fix == "uv python install 3.15t"


def test_gil_still_enabled_on_315t_warns(tmp_path: Path) -> None:
    """If 3.15t reports the GIL as enabled, D6 warns: K4's experiments would be misleading."""
    answers = healthy_answers()
    answers["3.15t -c import sys; print(sys._is_gil_enabled())"] = RunResult(0, "True")
    assert (
        doctor.check_d6(make_context(tmp_path, run=FakeRunner(answers).run)).status is Status.WARN
    )


def test_venv_on_mnt_c_warns(tmp_path: Path) -> None:
    """A venv on /mnt/c (slow 9P) makes D7 warn and points at dev.sh (ADR-003)."""
    context = make_context(tmp_path, sys_prefix="/mnt/c/Users/x/Kiln/.venv")
    result = doctor.check_d7(context)
    assert result.status is Status.WARN
    assert "dev.sh" in result.fix


def test_missing_compiler_warns_until_k4(tmp_path: Path) -> None:
    """No `cc` is only a warning by default, but a failure when preparing for K4 or later."""
    context = make_context(tmp_path, which=which_without_cc)
    plain = by_id(doctor.run_checks(context))
    assert plain["D9"].status is Status.WARN
    for_k3 = by_id(doctor.run_checks(context, milestone=3))
    assert for_k3["D9"].status is Status.WARN
    for_k4 = by_id(doctor.run_checks(context, milestone=4))
    assert for_k4["D9"].status is Status.FAIL
    assert doctor.exit_code(list(for_k4.values())) == 1


def test_ptrace_scope_one_warns_with_sysctl_fix(tmp_path: Path) -> None:
    """ptrace_scope = 1 (this WSL's default) warns and suggests the temporary sysctl."""
    context = make_context(tmp_path)
    (context.proc_root / "sys" / "kernel" / "yama" / "ptrace_scope").write_text("1\n")
    result = doctor.check_d11(context)
    assert result.status is Status.WARN
    assert "sysctl kernel.yama.ptrace_scope=0" in result.fix


def test_no_cgroup_method_warns(tmp_path: Path) -> None:
    """Without systemd-run or passwordless sudo, the OOM lab can't make a cgroup: D13 warns."""
    answers = healthy_answers()
    answers.pop("systemd-run --user --scope --quiet true")
    assert (
        doctor.check_d13(make_context(tmp_path, run=FakeRunner(answers).run)).status is Status.WARN
    )


def test_unreadable_smaps_rollup_fails(tmp_path: Path) -> None:
    """Without /proc/self/smaps_rollup, K3's memory measurements are impossible: D14 fails."""
    context = make_context(tmp_path)
    (context.proc_root / "self" / "smaps_rollup").unlink()
    assert doctor.check_d14(context).status is Status.FAIL


def test_ram_is_reported_in_gib(tmp_path: Path) -> None:
    """D15 converts /proc/meminfo's KiB into GiB: 7864320 KiB is 7.5 GiB."""
    result = doctor.check_d15(make_context(tmp_path))
    assert result.status is Status.INFO
    assert "7.5 GiB" in result.detail


def test_crlf_in_scripts_fails_d16(tmp_path: Path) -> None:
    """A shell script with Windows line endings fails D16 and names the file."""
    context = make_context(tmp_path)
    (context.repo_root / "scripts" / "bad.sh").write_bytes(b"echo hi\r\n")
    result = doctor.check_d16(context)
    assert result.status is Status.FAIL
    assert "bad.sh" in result.detail


def test_busy_port_warns(tmp_path: Path) -> None:
    """If port 7340 is taken by another program, D18 warns and names the port."""
    result = doctor.check_d18(make_context(tmp_path, port_free=lambda port: port != 7340))
    assert result.status is Status.WARN
    assert "7340" in result.detail


def test_missing_debug_tools_warn(tmp_path: Path) -> None:
    """On an interpreter without Tachyon or remote_exec (older than 3.15), D19 warns."""
    context = make_context(tmp_path, module_exists=lambda name: False, has_remote_exec=False)
    assert doctor.check_d19(context).status is Status.WARN


def test_command_timeouts_never_crash_doctor(tmp_path: Path) -> None:
    """A command that times out counts as "not working", it never raises (K0 spec §5.6)."""
    timed_out = RunResult(returncode=-1, stdout="", timed_out=True)
    answers = dict.fromkeys(healthy_answers(), timed_out)
    results = doctor.run_checks(make_context(tmp_path, run=FakeRunner(answers).run))
    assert by_id(results)["D10"].status is Status.WARN


@pytest.mark.parametrize(("text", "number"), [("K4", 4), ("k10", 10), ("0", 0), (" K1 ", 1)])
def test_parse_milestone_accepts_common_spellings(text: str, number: int) -> None:
    """--milestone accepts "K4", "k10", "0"..., so people can type it however they like."""
    assert doctor.parse_milestone(text) == number


@pytest.mark.parametrize("text", ["K11", "x", "", "K-1"])
def test_parse_milestone_rejects_nonsense(text: str) -> None:
    """Anything that isn't K0..K10 is rejected with ValueError (the CLI turns it into exit 2)."""
    with pytest.raises(ValueError, match=r"K0\.\.K10"):
        doctor.parse_milestone(text)


def test_table_shows_fix_lines_and_summary(tmp_path: Path) -> None:
    """The table prints a `fix:` line under each warning and ends with a summary line."""
    context = make_context(tmp_path, which=which_without_cc)
    table = doctor.render_table(doctor.run_checks(context))
    assert "fix (needed by K4): sudo apt install build-essential" in table
    assert table.splitlines()[-1] == "0 failed, 1 warnings, 19 checks"


def test_json_output_is_parseable_with_all_fields(tmp_path: Path) -> None:
    """`--json` output parses and has id/title/status/detail/fix/needed_by for each check."""
    rows = json.loads(doctor.render_json(doctor.run_checks(make_context(tmp_path))))
    assert len(rows) == 19
    assert set(rows[0]) == {"id", "title", "status", "detail", "fix", "needed_by"}


def test_real_machine_doctor_runs_without_crashing() -> None:
    """Doctor runs against this real machine and returns one result per check.

    It must never crash, whatever the machine is missing (the statuses themselves vary by
    machine, so they aren't asserted).
    """
    results = doctor.run_checks(DoctorContext.real())
    assert len(results) == 19
