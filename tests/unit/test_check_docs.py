"""Tests for scripts/check_docs.py: each DOC check catches a bad example and accepts a good one.

For every check id there is a tiny "repository" made of a few files. The bad version breaks
exactly that one rule; the good version follows it. Both are written into a temporary folder,
and the check is run against it (AC-0.6). A check that never fails proves nothing, so the bad
case matters as much as the good one.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

GOOD_MODULE_DOC = '''"""A tiny module.

WHY THIS MODULE EXISTS
    For a test.

HOW IT FITS
    Nowhere.

READ FIRST
    Nothing.

MILESTONE
    K0.
"""
'''

PRIMER_BODY = (
    "# P\n\n> **Terms introduced:** **widget**\n\n"
    + "".join(
        f"## {h}\n\ntext\n\n"
        for h in (
            "One-sentence summary",
            "Why you should care",
            "The mental model",
            "See it for yourself",
            "How it really works",
            "How Kiln uses it",
            "Common misconceptions",
            "Check yourself",
            "Go deeper",
        )
    )
    + "<details>a</details>\n<details>b</details>\n<details>c</details>\n"
)

NOTE_BODY = "# N\n\n> **Terms introduced:** **widget**\n\n" + "".join(
    f"## {h}\n\ntext\n\n"
    for h in (
        "What it looks like",
        "What it means",
        "Why people use it",
        "How Kiln uses it",
        "Gotchas",
        "Try it",
        "Check yourself",
    )
)

LAB_HEADINGS = (
    "The incident",
    "Your prediction",
    "Verified facts",
    "Setup",
    "Method",
    "Results: before",
    "The fix",
    "Results: after",
    "Analysis",
    "Reference comparison",
    "Limitations and threats to validity",
    "What you should now be able to explain",
    "Checkpoint quiz",
)
LAB_BODY = "# Lab\n\n" + "".join(
    f"## {n}. {h}\n\ntext\n\n" for n, h in enumerate(LAB_HEADINGS, start=1)
)

LEDGER = "# Notes\n\n## 2. The ledger\n\n| Feature | Note |\n|---|---|\n| `f-strings` | x |\n"
LONG_BODY = "    a = 1\n    b = 2\n    c = 3\n    d = 4\n    e = 5\n    return a + b + c + d + e\n"
DOC_NO_WHY = '    """Add numbers."""\n'
DOC_WITH_WHY = '    """Add numbers.\n\n    Why:\n        Because.\n    """\n'
DELIVERABLES = "# K5\n\n## 10. Docs deliverables\n\n| Kind | Path |\n|---|---|\n| Concept | `docs/concepts/zzz.md` |\n"
STATUS_TABLE = "| # | Milestone | Spec | Status |\n|---|---|---|---|\n| K5 | x | [K5](K5-x.md) | 🚧 in progress |\n"

# check id → (files of the BAD repo, extra/changed files that make it GOOD)
CASES: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "DOC001": ({"src/kiln/x.py": "x = 1\n"}, {"src/kiln/x.py": GOOD_MODULE_DOC + "x = 1\n"}),
    "DOC002": (
        {"src/kiln/x.py": '"""A module without the four headings."""\n'},
        {"src/kiln/x.py": GOOD_MODULE_DOC},
    ),
    "DOC003": (
        {"src/kiln/x.py": GOOD_MODULE_DOC + "def f():\n    pass\n"},
        {"src/kiln/x.py": GOOD_MODULE_DOC + 'def f():\n    """Do nothing."""\n'},
    ),
    "DOC004": (
        {"src/kiln/x.py": GOOD_MODULE_DOC + "def f():\n" + DOC_NO_WHY + LONG_BODY},
        {"src/kiln/x.py": GOOD_MODULE_DOC + "def f():\n" + DOC_WITH_WHY + LONG_BODY},
    ),
    "DOC005": (
        {"src/kiln/x.py": 'x = f"{1}"\n', "docs/python/README.md": "# Notes\n\n## 2. The ledger\n"},
        {"docs/python/README.md": LEDGER},
    ),
    "DOC006": (
        {"src/kiln/x.py": "x = 1  # CONCEPT(fork): two processes now\n"},
        {"docs/concepts/fork.md": "# Fork\n"},
    ),
    "DOC007": (
        {"src/kiln/x.py": "x = 1  # INCIDENT(instagram-cow): the freeze recipe\n"},
        {"docs/research/incidents.md": "# Incidents\n\n## instagram-cow\n"},
    ),
    "DOC008": (
        {"docs/a.md": "See [b](b.md#title) and [c](missing.md).\n", "docs/b.md": "# Other\n"},
        {"docs/a.md": "See [b](b.md#title).\n", "docs/b.md": "# Title\n"},
    ),
    "DOC009": (
        {"docs/concepts/p.md": "# P\n\n## One-sentence summary\n"},
        {"docs/concepts/p.md": PRIMER_BODY},
    ),
    "DOC010": (
        {"docs/concepts/p.md": "<details>a</details><details>b</details>"},
        {"docs/concepts/p.md": PRIMER_BODY},
    ),
    "DOC011": (
        {"docs/concepts/p.md": "Run docs/concepts/snippets/p_1.py now.\n"},
        {"docs/concepts/snippets/p_1.py": "print(1)\n"},
    ),
    "DOC012": (
        {"docs/labs/K1-x.md": "# Lab\n\n## 1. The incident\n"},
        {"docs/labs/K1-x.md": LAB_BODY},
    ),
    "DOC013": (
        {"docs/labs/K1-x.md": "## 6. Results: before\n\n| a | b |\n|---|---|\n| 1 |  |\n"},
        {
            "docs/labs/K1-x.md": "## 6. Results: before\n\n| a | b |\n|---|---|\n| 1 | not measured (x) |\n"
        },
    ),
    "DOC014": (
        {"docs/python/n.md": NOTE_BODY, "docs/glossary.md": "# Glossary\n"},
        {"docs/glossary.md": "# Glossary\n\n- **Widget.** A thing.\n"},
    ),
    "DOC015": (
        {"tests/test_x.py": "def test_a():\n    assert True\n"},
        {"tests/test_x.py": 'def test_a():\n    """Pin down a."""\n    assert True\n'},
    ),
    "DOC016": (
        {"docs/milestones/README.md": STATUS_TABLE, "docs/milestones/K5-x.md": DELIVERABLES},
        {"docs/concepts/zzz.md": "# Z\n"},
    ),
    "DOC017": (
        {"docs/python/n.md": "# N\n\n## What it looks like\n"},
        {"docs/python/n.md": NOTE_BODY},
    ),
}


def load_check_docs() -> ModuleType:
    """Import scripts/check_docs.py as a module (scripts/ is not a package).

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location(
        "check_docs", REPO_ROOT / "scripts" / "check_docs.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_docs"] = module
    spec.loader.exec_module(module)
    return module


check_docs = load_check_docs()


def write_tree(root: Path, files: dict[str, str]) -> None:
    """Write a dict of {relative path: text} as real files under root.

    Args:
        root: the fake repository's root folder.
        files: the files to create or overwrite.
    """
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_every_check_has_a_case() -> None:
    """Every DOC id in check_docs.CHECKS has a good/bad case here, and vice versa (AC-0.6)."""
    assert sorted(CASES) == sorted(check_docs.CHECKS)


@pytest.mark.parametrize("check_id", sorted(CASES))
def test_check_catches_bad_and_accepts_good(check_id: str, tmp_path: Path) -> None:
    """Each check reports the bad mini-repo and finds nothing in the good one (AC-0.6)."""
    bad_files, good_changes = CASES[check_id]
    write_tree(tmp_path, bad_files)
    bad = check_docs.run(tmp_path, [check_id])
    assert bad, f"{check_id} did not catch its bad example"
    assert all(problem.code == check_id for problem in bad)
    write_tree(tmp_path, good_changes)
    good = check_docs.run(tmp_path, [check_id])
    assert good == [], f"{check_id} rejected its good example: {good}"


def test_problem_line_format() -> None:
    """Problems print as `path:line: DOCnnn message (fix: ...)`, like compiler errors."""
    problem = check_docs.Problem("a.py", 3, "DOC001", "module has no docstring", "add one")
    assert problem.render() == "a.py:3: DOC001 module has no docstring (fix: add one)"


def test_unknown_check_id_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    """`--only DOC999` exits with 2 and names the unknown id."""
    assert check_docs.main(["--only", "DOC999"]) == 2
    assert "DOC999" in capsys.readouterr().err


def test_github_slug_matches_githubs_rules() -> None:
    """Anchors are computed like GitHub does, so `#5-shutdown` links resolve the same way."""
    assert check_docs.github_slug("5. Shutdown") == "5-shutdown"
    assert check_docs.github_slug("`kiln doctor` checks (K0)") == "kiln-doctor-checks-k0"


def test_constructs_are_found_with_their_first_line() -> None:
    """The construct map finds features (and where they first appear) for the Python ledger."""
    source = 'import os\n\n\ndef f(*args):\n    return [x for x in args]\n\n\nname = f"{os.sep}"\n'
    found = check_docs.constructs_in(check_docs.ast.parse(source))
    assert found["modules-and-imports"] == 1
    assert found["functions"] == 4
    assert found["star-args-and-unpacking"] == 4
    assert found["comprehensions"] == 5
    assert found["f-strings"] == 8


def test_planning_docs_may_link_to_future_files(tmp_path: Path) -> None:
    """A milestone spec may link to a lab report that a later milestone writes; a primer may not."""
    write_tree(tmp_path, {"docs/milestones/K9.md": "[r](../labs/K9-matrix.md)\n"})
    assert check_docs.run(tmp_path, ["DOC008"]) == []
    write_tree(tmp_path, {"docs/concepts/p.md": "[r](../labs/K9-matrix.md)\n"})
    assert len(check_docs.run(tmp_path, ["DOC008"])) == 1
