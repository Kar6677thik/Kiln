"""Run every documentation snippet so the docs can never quietly go stale (spec/09 §7).

Each `docs/concepts/snippets/*.py` and `docs/python/snippets/*.py` is run in a fresh
interpreter. If a sibling `<name>.expected.txt` exists, the output must match it, where these
placeholders stand for parts that change from run to run:

    <NUM>  any number          <PID>  a process id     <ADDR>  a hex address like 0x7f3a...
    <MS>   a duration number   <ANY>  any text up to the end of the match on that line

A snippet can declare what it needs in a header comment, for example `# requires: linux, py>=3.14`.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SNIPPET_DIRS = (
    REPO_ROOT / "docs" / "concepts" / "snippets",
    REPO_ROOT / "docs" / "python" / "snippets",
)
PLACEHOLDERS = {
    "<NUM>": r"-?\d+(?:\.\d+)?",
    "<PID>": r"\d+",
    "<ADDR>": r"0x[0-9a-fA-F]+",
    "<MS>": r"\d+(?:\.\d+)?",
    "<ANY>": r".*?",
}


def all_snippets() -> list[Path]:
    """Find every snippet file (top level of each snippets folder; helper packages excluded).

    Returns:
        Sorted list of .py paths.
    """
    found: list[Path] = []
    for folder in SNIPPET_DIRS:
        if folder.exists():
            found += folder.glob("*.py")
    return sorted(found)


def requirement_skip_reason(path: Path) -> str | None:
    """Read a snippet's `# requires:` header and explain why it can't run here, if so.

    Args:
        path: the snippet.

    Returns:
        A skip reason, or None if it can run.
    """
    for line in path.read_text(encoding="utf-8").splitlines()[:5]:
        if line.startswith("# requires:"):
            for need in (part.strip() for part in line.split(":", 1)[1].split(",")):
                if need == "linux" and sys.platform != "linux":
                    return "snippet needs Linux"
                match = re.fullmatch(r"py>=(\d+)\.(\d+)", need)
                if match and sys.version_info < (int(match.group(1)), int(match.group(2))):
                    return f"snippet needs Python >= {match.group(1)}.{match.group(2)}"
    return None


def line_pattern(expected_line: str) -> re.Pattern[str]:
    """Turn one expected line (with placeholders) into a regular expression.

    Args:
        expected_line: a line from a .expected.txt file.

    Returns:
        A compiled pattern that must match the whole actual line.
    """
    pattern = re.escape(expected_line)
    for placeholder, regex in PLACEHOLDERS.items():
        pattern = pattern.replace(re.escape(placeholder), regex)
    return re.compile(pattern)


@pytest.mark.snippets
@pytest.mark.parametrize("path", all_snippets(), ids=lambda p: p.name)
def test_snippet_runs_and_matches_expected_output(path: Path) -> None:
    """The snippet exits 0 (in under 20 s) and, if an expected file exists, prints matching text.

    A primer that says "this prints X" must stay true as Python versions change.
    """
    reason = requirement_skip_reason(path)
    if reason:
        pytest.skip(reason)
    result = subprocess.run(
        [sys.executable, str(path)],
        capture_output=True,
        text=True,
        timeout=20,
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0, f"{path.name} failed:\n{result.stdout}\n{result.stderr}"
    expected_file = path.with_suffix(".expected.txt")
    if not expected_file.exists():
        return
    expected = expected_file.read_text(encoding="utf-8").rstrip("\n").splitlines()
    actual = result.stdout.rstrip("\n").splitlines()
    assert len(actual) == len(expected), f"line count differs:\n{result.stdout}"
    for number, (want, got) in enumerate(zip(expected, actual, strict=True), start=1):
        assert line_pattern(want).fullmatch(got), f"line {number}: expected {want!r}, got {got!r}"
