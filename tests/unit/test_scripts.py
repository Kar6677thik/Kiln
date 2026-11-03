"""Tests for the helper scripts: safe quoting in shell scripts, and honest REPL transcripts."""

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_script(name: str) -> ModuleType:
    """Import scripts/<name>.py as a module.

    Args:
        name: the script's file name without .py.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_scripts_quote_paths() -> None:
    """Shell scripts never use $REPO_ROOT or $HOME unquoted (spec/07 §10).

    The repo path contains a space ("Karthik Reddy"); an unquoted variable would be split into
    two words and break the command in a confusing way.
    """
    unquoted = re.compile(r'(?<!["\w])\$(?:\{)?(REPO_ROOT|HOME)\b')
    problems = []
    for script in sorted((REPO_ROOT / "scripts").glob("*.sh")):
        for number, line in enumerate(script.read_text(encoding="utf-8").splitlines(), start=1):
            code = line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
            # WHY: inside "..." the variable is safe; only look at text outside double quotes.
            outside_quotes = re.sub(r'"[^"]*"', '""', code)
            if unquoted.search(outside_quotes):
                problems.append(f"{script.name}:{number}: {line.strip()}")
    assert problems == []


def test_shell_scripts_use_strict_mode() -> None:
    """Every shell script starts with a bash shebang and `set -euo pipefail` (K0 spec §5.5)."""
    for script in sorted((REPO_ROOT / "scripts").glob("*.sh")):
        text = script.read_text(encoding="utf-8")
        assert text.startswith("#!/usr/bin/env bash\n"), script.name
        assert "set -euo pipefail" in text, script.name


def test_repl_transcript_shows_prompts_values_and_errors() -> None:
    """repl_transcript.py records real output: prompts, printed values, and tracebacks."""
    transcript = load_script("repl_transcript").make_transcript(
        ["x = [1, 2]", "x + [3]", "def f(a):", "    return a * 2", "", "f(21)", "1/0"]
    )
    lines = transcript.splitlines()
    assert lines[:3] == [">>> x = [1, 2]", ">>> x + [3]", "[1, 2, 3]"]
    assert "...     return a * 2" in lines
    assert ">>> f(21)" in lines
    assert "42" in lines
    assert lines[-1] == "ZeroDivisionError: division by zero"
