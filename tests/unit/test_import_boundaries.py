"""Enforce which Kiln packages may import which (docs/spec/01-architecture.md §5).

Why boundaries matter: they keep each part of Kiln understandable on its own, and they stop the
master from ever importing worker or user code by accident. The master must stay single-threaded
and must never run task code (ADR-006); a stray import is the easiest way to break that.

The allow-table is the one in docs/milestones/K0-foundations.md §5.8. Changing it requires an ADR.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Imported by everything: the "core layer".
CORE = {"kiln.errors", "kiln.logsetup"}

# package → packages it may import (besides itself, the stdlib and the core layer).
# "*" means "anything".
ALLOWED: dict[str, set[str]] = {
    "kiln.errors": set(),
    "kiln.logsetup": set(),
    "kiln.protocol": set(),
    "kiln.config": set(),
    "kiln.app": {"kiln.config"},  # ADR-033: manifest defaults come from JobsConfig
    "kiln.memory": set(),
    "kiln.forkcheck": set(),
    "kiln.native": set(),
    "kiln.labs": {"kiln.memory", "kiln.app"},  # ADR-033: register() adds tasks to an App
    "kiln.client": {"kiln.protocol", "kiln.config"},
    "kiln.worker": {"kiln.protocol", "kiln.config", "kiln.app", "kiln.memory", "kiln.labs"},
    "kiln.launch": {
        "kiln.protocol",
        "kiln.config",
        "kiln.memory",
        "kiln.worker",
        "kiln.app",
    },  # ADR-033
    "kiln.master": {
        "kiln.protocol",
        "kiln.config",
        "kiln.app",
        "kiln.memory",
        "kiln.launch",
        "kiln.labs",
        "kiln.__init__",  # ADR-033: only for __version__
    },
    "kiln.gateway": {"kiln.protocol", "kiln.config", "kiln.bench.records"},
    "kiln.bench": {"kiln.protocol", "kiln.config", "kiln.client"},
    "kiln.cli": {"*"},
    "kiln.doctor": {"*"},
    "kiln.__init__": {"*"},
    "kiln.__main__": {"*"},
}


def module_name(src_root: Path, path: Path) -> str:
    """Turn src/kiln/master/arbiter.py into "kiln.master.arbiter".

    Args:
        src_root: the src/ folder.
        path: a .py file below it.

    Returns:
        The dotted module name ("kiln.__init__" for the package's own __init__.py).
    """
    parts = list(path.relative_to(src_root).with_suffix("").parts)
    if parts[-1] == "__init__" and len(parts) > 1 and parts[:-1] != ["kiln"]:
        parts = parts[:-1]
    return ".".join(parts)


def package_of(module: str) -> str:
    """Return the top-level Kiln package a module belongs to: "kiln.master.arbiter" → "kiln.master".

    Args:
        module: a dotted module name inside kiln.

    Returns:
        "kiln.<first part>".
    """
    parts = module.split(".")
    return ".".join(parts[:2])


def imported_modules(src_root: Path, path: Path) -> list[tuple[int, str]]:
    """List the Kiln modules a file imports, resolving relative imports.

    Why:
        `from ..protocol import framing` and `from kiln import doctor` both name Kiln modules,
        in different spellings; the check needs one absolute spelling.

    Args:
        src_root: the src/ folder.
        path: the file to inspect.

    Returns:
        (line, absolute module name) for each import of a kiln module.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    this = module_name(src_root, path).split(".")
    package = this if path.name == "__init__.py" else this[:-1]
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - node.level + 1]
                origin = ".".join(base + ([node.module] if node.module else []))
            else:
                origin = node.module or ""
            for alias in node.names:
                candidate = f"{origin}.{alias.name}"
                # WHY: "from kiln import doctor" imports the module kiln.doctor, while
                # "from kiln import __version__" imports a name defined in kiln/__init__.py.
                is_module = (src_root / Path(*candidate.split("."))).with_suffix(".py").exists()
                is_package = (src_root / Path(*candidate.split("."))).is_dir()
                found.append((node.lineno, candidate if is_module or is_package else origin))
    return [(line, name) for line, name in found if name == "kiln" or name.startswith("kiln.")]


def is_allowed(source_package: str, target: str) -> bool:
    """Decide whether a module in `source_package` may import `target`.

    Args:
        source_package: such as "kiln.master".
        target: such as "kiln.worker.main".

    Returns:
        True if the allow-table permits it.
    """
    if target == "kiln":
        target = "kiln.__init__"
    target_package = package_of(target)
    if target_package == source_package or target_package in CORE:
        return True
    allowed = ALLOWED.get(source_package, set())
    if "*" in allowed:
        return True
    return any(target == entry or target.startswith(entry + ".") for entry in allowed) or (
        target_package in allowed
    )


def boundary_violations(src_root: Path) -> list[str]:
    """Check every file under src_root/kiln against the allow-table.

    Why:
        Returns messages instead of asserting, so tests can check both a clean tree and a
        deliberately broken one.

    Args:
        src_root: a src/ folder containing a kiln package.

    Returns:
        One message per forbidden import (empty if everything is allowed).
    """
    problems = []
    for path in sorted((src_root / "kiln").rglob("*.py")):
        source = module_name(src_root, path)
        source_package = source if source in ALLOWED else package_of(source)
        if source_package not in ALLOWED:
            problems.append(f"{path}: package {source_package} is missing from the allow-table")
            continue
        for line, target in imported_modules(src_root, path):
            if not is_allowed(source_package, target):
                problems.append(
                    f"{path.relative_to(src_root.parent)}:{line} imports {target}"
                    f" — {source_package} must not import it (spec/01 §5)"
                )
    return problems


def test_kiln_respects_its_import_boundaries() -> None:
    """Every import in src/kiln follows the allow-table (AC-0.8)."""
    assert boundary_violations(REPO_ROOT / "src") == []


def test_a_planted_bad_import_is_caught(tmp_path: Path) -> None:
    """The check really fails when the master imports the worker (AC-0.8).

    A test that can never fail proves nothing, so we build a tiny fake src tree that breaks the
    most important rule ("master and worker never import each other") and expect a report.
    """
    kiln_dir = tmp_path / "src" / "kiln"
    (kiln_dir / "master").mkdir(parents=True)
    (kiln_dir / "worker").mkdir()
    (kiln_dir / "__init__.py").write_text("")
    (kiln_dir / "master" / "__init__.py").write_text("")
    (kiln_dir / "worker" / "__init__.py").write_text("")
    (kiln_dir / "worker" / "main.py").write_text("def run_worker():\n    pass\n")
    (kiln_dir / "master" / "arbiter.py").write_text("from kiln.worker import main\n")
    problems = boundary_violations(tmp_path / "src")
    assert len(problems) == 1
    assert "kiln.worker.main" in problems[0]
    assert "kiln.master must not import it" in problems[0]


def test_relative_imports_are_resolved(tmp_path: Path) -> None:
    """`from ..worker import main` inside kiln/master is caught too, not only absolute imports."""
    kiln_dir = tmp_path / "src" / "kiln"
    (kiln_dir / "master").mkdir(parents=True)
    (kiln_dir / "worker").mkdir()
    (kiln_dir / "__init__.py").write_text("")
    (kiln_dir / "master" / "__init__.py").write_text("")
    (kiln_dir / "worker" / "__init__.py").write_text("")
    (kiln_dir / "worker" / "main.py").write_text("")
    (kiln_dir / "master" / "arbiter.py").write_text("from ..worker import main\n")
    assert len(boundary_violations(tmp_path / "src")) == 1
