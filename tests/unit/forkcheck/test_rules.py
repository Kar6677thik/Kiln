"""Tests for the rules FA001–FA008, driven by the corpus (§5.10).

Every synthetic file says what must fire with `# expect: FA00x` comments; anything else that
fires is a false positive. The real cases' expected.json files list must-fire findings (exact
line) and rules that must not fire at all.
"""

import json
import re
import textwrap
from pathlib import Path

import pytest

from kiln.forkcheck import scan
from kiln.forkcheck.rules import RULE_INFO, RULES, Finding

ROOT = Path(__file__).resolve().parents[3]
SYNTHETIC = ROOT / "corpus" / "synthetic"
REAL = ROOT / "corpus" / "real"
EXPECT = re.compile(r"#\s*expect:\s*((?:FA\d{3}[,\s]*)+)")
SYNTHETIC_FILES = sorted(SYNTHETIC.glob("FA00*/*.py"))
REAL_CASES = sorted(p.parent for p in REAL.glob("*/expected.json"))


def expectations(path: Path) -> set[tuple[int, str]]:
    """(line, rule) pairs from the file's `# expect:` comments."""
    found = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = EXPECT.search(line)
        if match is not None:
            found.update((number, rule) for rule in re.findall(r"FA\d{3}", match.group(1)))
    return found


def findings_for(path: Path) -> list[Finding]:
    """Scan the file's folder (so cross-module imports resolve) and keep this file's findings."""
    result = scan([path.parent])
    return [f for f in result.findings if Path(f.path).name == path.name]


def scan_source(tmp_path: Path, source: str, name: str = "m.py") -> list[Finding]:
    """Write a snippet to a temporary file and scan it."""
    target = tmp_path / name
    target.write_bytes(textwrap.dedent(source).encode())
    return scan([target]).findings


def test_corpus_meets_the_synthetic_minimum() -> None:
    """At least 4 pos_ and 4 neg_ files exist for every rule FA001–FA007 (§5.10)."""
    for number in range(1, 8):
        folder = SYNTHETIC / f"FA00{number}"
        assert len(list(folder.glob("pos_*.py"))) >= 4, folder
        assert len(list(folder.glob("neg_*.py"))) >= 4, folder


@pytest.mark.parametrize("path", SYNTHETIC_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_synthetic_file_fires_exactly_what_it_expects(path: Path) -> None:
    """Each synthetic file produces exactly its `# expect:` findings: no misses, no extras."""
    actual = {(f.line, f.rule) for f in findings_for(path)}
    assert actual == expectations(path)


@pytest.mark.parametrize("case", REAL_CASES, ids=lambda p: p.name)
def test_real_case_must_fire_and_must_not_fire(case: Path) -> None:
    """Real distilled cases: every must-fire finding on its exact line, no must-not-fire rule."""
    expected = json.loads((case / "expected.json").read_text(encoding="utf-8"))
    findings = scan([case]).findings
    found = {(Path(f.path).name, f.line, f.rule) for f in findings}
    for item in expected["must_fire"]:
        assert (item["path"], item["line"], item["rule"]) in found, item
    fired = {f.rule for f in findings}
    for item in expected["must_not_fire"]:
        assert item["rule"] not in fired, item


def test_every_rule_is_registered_with_a_name() -> None:
    """RULES has FA001–FA008, and every rule (plus FA000) has a name and motivation."""
    assert sorted(RULES) == [f"FA00{n}" for n in range(1, 9)]
    for rule in [*RULES, "FA000"]:
        assert rule in RULE_INFO


def test_forced_fork_downgrades_fa001_to_info() -> None:
    """FA001 known false positive: set_start_method("fork") first → FA001 is only info."""
    findings = findings_for(SYNTHETIC / "FA001" / "pos_forced_fork_info.py")
    severities = {f.rule: f.severity for f in findings}
    assert severities == {"FA001": "info", "FA008": "info"}


def test_fork_context_makes_fa002_info() -> None:
    """FA002 with an explicit get_context("fork") receiver works today, so it is info."""
    findings = findings_for(SYNTHETIC / "FA002" / "pos_fork_context_info.py")
    assert {f.rule: f.severity for f in findings}["FA002"] == "info"


def test_pool_targets_break_under_fork_too() -> None:
    """Pool.map pickles its function under every method, so FA002 lists fork as well."""
    (finding,) = findings_for(SYNTHETIC / "FA002" / "pos_pool_nested.py")
    assert set(finding.breaks_under) == {"fork", "spawn", "forkserver"}


def test_process_targets_break_only_without_fork() -> None:
    """A nested Process target works under fork (nothing is pickled), so fork isn't listed."""
    (finding,) = findings_for(SYNTHETIC / "FA002" / "pos_nested_function.py")
    assert finding.breaks_under == ("spawn", "forkserver")
    assert finding.severity == "error"


def test_fa005_names_the_importing_starter() -> None:
    """FA005 in a helper names the module that imports it and starts processes."""
    (finding,) = findings_for(SYNTHETIC / "FA005" / "pos_helper_logfile.py")
    assert "pos_imports_helper.py" in finding.message


def test_suppression_comment_for_one_rule(tmp_path: Path) -> None:
    """`# forkcheck: ignore[FA001]` on the finding's line silences that rule."""
    source = """
        import multiprocessing as mp
        p = mp.Process(target=print)  # forkcheck: ignore[FA001]
    """
    assert scan_source(tmp_path, source) == []


def test_suppression_comment_for_other_rule_does_not_silence(tmp_path: Path) -> None:
    """Ignoring a different rule leaves the finding in place."""
    source = """
        import multiprocessing as mp
        p = mp.Process(target=print)  # forkcheck: ignore[FA003]
    """
    assert [f.rule for f in scan_source(tmp_path, source)] == ["FA001"]


def test_bare_suppression_silences_every_rule(tmp_path: Path) -> None:
    """`# forkcheck: ignore` with no list silences all rules on that line."""
    source = """
        import multiprocessing as mp
        p = mp.Process(target=lambda: None)  # forkcheck: ignore
    """
    assert scan_source(tmp_path, source) == []


def test_parse_error_is_fa000_and_the_scan_continues(tmp_path: Path) -> None:
    """A file that doesn't parse gives FA000 (info), and other files are still checked."""
    (tmp_path / "broken.py").write_bytes(b"def broken(:\n")
    (tmp_path / "ok.py").write_bytes(b"import multiprocessing as mp\nmp.Process(target=print)\n")
    result = scan([tmp_path])
    assert [(Path(f.path).name, f.rule) for f in result.findings] == [
        ("broken.py", "FA000"),
        ("ok.py", "FA001"),
    ]
    assert result.parse_errors == 1


def test_excludes_skip_matching_paths(tmp_path: Path) -> None:
    """--exclude globs (and the default .venv) are skipped."""
    for folder in ("skipme", ".venv", "kept"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "m.py").write_bytes(b"import multiprocessing as mp\nmp.Manager()\n")
    result = scan([tmp_path], excludes=["skipme"])
    assert {Path(f.path).parent.name for f in result.findings} == {"kept"}
    assert result.files_scanned == 1
