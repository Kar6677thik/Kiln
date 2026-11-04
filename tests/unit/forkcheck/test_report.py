"""Tests for forkcheck's output formats and exit codes (§5.8, AC-2.6).

The text and JSON reports for two real corpus cases are pinned by golden files in
tests/unit/forkcheck/goldens/. If a change to a message is intended, regenerate them with
`KILN_UPDATE_GOLDENS=1 ./scripts/dev.sh test tests/unit/forkcheck/test_report.py` and review
the diff.
"""

import json
import os
from pathlib import Path

import pytest

from kiln.forkcheck import main
from kiln.forkcheck.report import exit_code, format_finding, summary_line
from kiln.forkcheck.rules import Finding

ROOT = Path(__file__).resolve().parents[3]
GOLDENS = Path(__file__).resolve().parent / "goldens"
CASES = ["oslo-lockutils", "krb5-t_otp"]


def finding(rule: str = "FA001", severity: str = "error") -> Finding:
    """A hand-made finding for format tests."""
    return Finding(
        rule=rule,
        name="unguarded-process-start",
        severity=severity,
        path="a.py",
        line=3,
        col=1,
        end_line=3,
        message="msg",
        hint="fix it",
        breaks_under=("spawn", "forkserver"),
        motivation="test",
    )


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    """Call main() from the repo root and return (exit code, stdout)."""
    code = main(argv)
    return code, capsys.readouterr().out


def check_golden(name: str, actual: str) -> None:
    """Compare with goldens/<name>, or rewrite it when KILN_UPDATE_GOLDENS=1."""
    path = GOLDENS / name
    if os.environ.get("KILN_UPDATE_GOLDENS") == "1":
        path.write_bytes(actual.encode("utf-8"))
    assert actual == path.read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def at_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Paths in reports are relative to the current folder, so run from the repo root."""
    monkeypatch.chdir(ROOT)


@pytest.mark.parametrize("case", CASES)
def test_text_output_matches_golden(case: str, capsys: pytest.CaptureFixture[str]) -> None:
    """The text report (findings + summary line) is byte-for-byte stable."""
    code, out = run([f"corpus/real/{case}"], capsys)
    assert code == 1
    check_golden(f"{case}.txt", out)


@pytest.mark.parametrize("case", CASES)
def test_json_output_matches_golden(case: str, capsys: pytest.CaptureFixture[str]) -> None:
    """The JSON report has the §5.8 shape and is byte-for-byte stable."""
    code, out = run([f"corpus/real/{case}", "--format", "json"], capsys)
    document = json.loads(out)
    assert code == 1
    assert document["tool"] == "kiln-forkcheck"
    assert document["version"] == 1
    assert list(document["findings"][0]) == [
        "rule", "name", "severity", "path", "line", "col", "end_line",
        "message", "hint", "breaks_under", "motivation", "confirmation",
    ]  # fmt: skip
    check_golden(f"{case}.json", out)


def test_text_line_shape() -> None:
    """One finding renders as `path:line:col: RULE [sev] name: message (breaks under) — hint`."""
    assert format_finding(finding()) == (
        "a.py:3:1: FA001 [error] unguarded-process-start: msg "
        "(breaks under: spawn, forkserver) — hint: fix it"
    )


def test_summary_line_counts_per_severity() -> None:
    """The summary counts each severity, the files scanned and the parse errors."""
    findings = [finding(), finding("FA003", "warning"), finding("FA003", "warning")]
    assert summary_line(findings, 1, 0) == (
        "1 error, 2 warnings, 0 infos in 1 file (0 parse errors)"
    )


@pytest.mark.parametrize(
    ("severity", "fail_on", "expected"),
    [
        ("error", "warning", 1),
        ("warning", "warning", 1),
        ("info", "warning", 0),
        ("warning", "error", 0),
        ("info", "info", 1),
    ],
)
def test_exit_code_follows_fail_on(severity: str, fail_on: str, expected: int) -> None:
    """Exit 1 only if some finding is at or above --fail-on (default warning)."""
    assert exit_code([finding(severity=severity)], fail_on) == expected


def test_parse_errors_alone_exit_zero() -> None:
    """FA000 never causes exit 1, even with --fail-on info (§5.8)."""
    assert exit_code([finding("FA000", "info")], "info") == 0


def test_clean_code_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """A file with no findings exits 0 and still prints the summary."""
    code, out = run(["corpus/synthetic/FA001/neg_guarded.py"], capsys)
    assert code == 0
    assert out == "0 errors, 0 warnings, 0 infos in 1 file (0 parse errors)\n"


def test_info_only_findings_pass_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    """FA008 is info: exit 0 by default, exit 1 with --fail-on info."""
    path = "corpus/synthetic/FA008/pos_os_fork.py"
    assert run([path], capsys)[0] == 0
    assert run([path, "--fail-on", "info"], capsys)[0] == 1


def test_missing_path_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    """A PATH that doesn't exist → exit 2 with a message on stderr."""
    assert main(["no/such/place"]) == 2
    assert "no such file" in capsys.readouterr().err


def test_bad_option_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    """Argparse errors become exit 2 instead of ending the interpreter."""
    assert main(["corpus", "--format", "xml"]) == 2
    assert "invalid choice" in capsys.readouterr().err
