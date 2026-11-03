"""Smoke tests for the `kiln` command line: does it start, and does it say who it is?"""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import kiln
from kiln import cli

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_version_text_names_kiln_python_and_build() -> None:
    """`kiln --version` starts with "kiln 0.1.0" and names the interpreter and build.

    AC-0.1: reports must say which Python and which build (gil/freethreaded) produced them.
    """
    text = cli.version_text()
    assert text.startswith("kiln 0.1.0 (")
    assert "build)" in text


def test_version_flag_prints_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """`kiln --version` prints the version line and exits with code 0 (argparse's convention)."""
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.startswith("kiln 0.1.0")


def test_python_dash_m_kiln_works() -> None:
    """`python -m kiln --version` works on this interpreter (it goes through __main__.py).

    This runs a real subprocess, exactly like typing the command, so it also proves the package
    is importable by a fresh interpreter (AC-0.1 runs this on all four matrix interpreters).
    """
    result = subprocess.run(
        [sys.executable, "-m", "kiln", "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("kiln 0.1.0")


def test_no_subcommand_shows_help_and_returns_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Running `kiln` with no command prints help and returns 2, never silently does nothing."""
    assert cli.main([]) == 2
    assert "usage: kiln" in capsys.readouterr().out


def test_bad_milestone_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    """`kiln doctor --milestone K99` is rejected with exit code 2 and an explanation."""
    assert cli.main(["doctor", "--milestone", "K99"]) == 2
    assert "K0..K10" in capsys.readouterr().err


def test_package_version_matches_pyproject() -> None:
    """`kiln.__version__` and pyproject.toml's version stay identical.

    Two copies of one fact drift apart unless a test ties them together.
    """
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == kiln.__version__
