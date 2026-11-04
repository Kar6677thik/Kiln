"""The forkcheck scanner: find files, build ModuleInfos, link imports, run every rule.

WHY THIS MODULE EXISTS
    Rules look at one module at a time, but some breakages span files: krb5's script imports a
    helper whose *import* registers an atexit handler (FA005). The scanner parses every file
    under the given paths, records which scanned module imports which, and hands the rules a
    Project that can answer "does any process-starting module import this one?". It also
    applies `# forkcheck: ignore[...]` suppressions and turns unparsable files into FA000.

HOW IT FITS
    forkcheck.main ──scan(paths, excludes, apis)──▶ ScanResult(findings, counts, project)
        files ──build_module──▶ ModuleInfo ──rules.RULES──▶ Finding ──suppressions──▶ result

READ FIRST
    docs/concepts/import-system.md, docs/concepts/static-vs-dynamic-analysis.md

MILESTONE
    Introduced in K2.
"""

import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from kiln.forkcheck.rules import RULES, Finding
from kiln.forkcheck.symbols import ModuleInfo, build_module

DEFAULT_EXCLUDES = (".venv", "__pycache__", "build", ".git")
SUPPRESS = re.compile(r"#\s*forkcheck:\s*ignore(?:\[([A-Z0-9,\s]+)\])?")
SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2}


def display_path(path: Path) -> str:
    """Show a path relative to the current folder when possible, with forward slashes.

    Args:
        path: an absolute path.

    Returns:
        The path as reports print it.
    """
    try:
        return Path(os.path.relpath(path)).as_posix()
    except ValueError:
        return path.as_posix()


@dataclass
class Project:
    """Every scanned module, plus the "who imports whom" links between them.

    Why:
        FA005 must know whether a module with import-time side effects is (transitively)
        imported by one that starts processes.
    """

    roots: list[Path]
    modules: dict[Path, ModuleInfo] = field(default_factory=dict)
    imports: dict[Path, set[Path]] = field(default_factory=dict)

    def _resolve_import(self, mod: ModuleInfo, dotted: str, level: int) -> Path | None:
        """Find the scanned file an import refers to, if it's one of ours.

        Why:
            Imports are resolved like Python does for local code: relative to the importing
            file's folder first (that's how a script's own folder is on sys.path), then each root.

        Args:
            mod: the importing module.
            dotted: the module name ("k5test_like", "pkg.helpers").
            level: the number of leading dots of a relative import.

        Returns:
            The file's path, or None for third-party or standard-library modules.
        """
        base = mod.path.parent
        for _ in range(max(level - 1, 0)):
            base = base.parent
        bases = [base] if level else [base, *self.roots]
        parts = [p for p in dotted.split(".") if p]
        for folder in bases:
            candidate = folder.joinpath(*parts) if parts else folder
            for path in (candidate.with_suffix(".py"), candidate / "__init__.py"):
                if path.resolve() in self.modules:
                    return path.resolve()
        return None

    def link(self) -> None:
        """Compute the import links between scanned modules.

        Why:
            Rules ask 'who imports this module?'; computing every link once is cheaper than
            resolving imports again for each question.
        """
        for path, mod in self.modules.items():
            targets = set()
            for dotted, level in mod.imports:
                found = self._resolve_import(mod, dotted, level)
                if found is not None and found != path:
                    targets.add(found)
            self.imports[path] = targets

    def imported_by(self, path: Path) -> set[Path]:
        """Every scanned module that imports `path`, directly or through others.

        Why:
            A side effect in a helper re-runs in a child even when the import is indirect (a imports
            b imports helper).

        Args:
            path: a module's path.

        Returns:
            The importers' paths.
        """
        found: set[Path] = set()
        frontier = {path}
        while frontier:
            current = frontier.pop()
            for importer, targets in self.imports.items():
                if current in targets and importer not in found:
                    found.add(importer)
                    frontier.add(importer)
        return found

    def starters_reaching(self, mod: ModuleInfo) -> list[tuple[ModuleInfo, int]]:
        """Modules that start processes and either are `mod` or import it (transitively).

        Why:
            Under spawn/forkserver, a child re-imports the main script and everything it imports;
            so `mod`'s import-time code re-runs in children of any such module.

        Args:
            mod: the module with possible side effects.

        Returns:
            (starter module, first site line) pairs, `mod` itself first if it starts processes.
        """
        result = []
        for path in [mod.path, *sorted(self.imported_by(mod.path))]:
            other = self.modules[path]
            creating = [s for s in other.sites if s.kind not in ("pool_call", "executor_call")]
            if creating:
                result.append((other, creating[0].line))
        return result


@dataclass
class ScanResult:
    """What a scan produced.

    Why:
        The report and the exit code need the findings and the counts together.
    """

    findings: list[Finding]
    files_scanned: int
    parse_errors: int
    project: Project


def collect_files(paths: list[Path], excludes: list[str]) -> list[Path]:
    """List the .py files under the given paths, skipping excluded names and globs.

    Why:
        Virtual environments and build folders contain thousands of files that aren't the user's
        code; scanning them would bury the real findings.

    Args:
        paths: files or folders.
        excludes: glob patterns (matched against each path part and the whole path).

    Returns:
        Sorted absolute paths.
    """
    patterns = list(DEFAULT_EXCLUDES) + excludes
    found = set()
    for root in paths:
        candidates = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in candidates:
            parts = path.parts
            text = path.as_posix()
            skip = any(fnmatch.fnmatch(part, pat) for part in parts for pat in patterns)
            if skip or any(fnmatch.fnmatch(text, pat) for pat in patterns):
                continue
            found.add(path.resolve())
    return sorted(found)


def is_suppressed(mod: ModuleInfo, finding: Finding) -> bool:
    """Check for `# forkcheck: ignore` or `# forkcheck: ignore[FA00x]` on the finding's line.

    Why:
        Static checks have false positives (§5.7); a comment on the line is the standard way to
        accept one, and it stays visible in review.

    Args:
        mod: the module.
        finding: the finding.

    Returns:
        True if the line suppresses this rule.
    """
    if not 1 <= finding.line <= len(mod.lines):
        return False
    match = SUPPRESS.search(mod.lines[finding.line - 1])
    if match is None:
        return False
    listed = match.group(1)
    return listed is None or finding.rule in {r.strip() for r in listed.split(",")}


def scan(
    paths: list[Path], excludes: list[str] | None = None, process_apis: list[str] | None = None
) -> ScanResult:
    """Scan files and folders and return every unsuppressed finding.

    Why:
        The one function the CLI and the tests call.

    Args:
        paths: files or folders to scan.
        excludes: extra glob patterns to skip.
        process_apis: user-declared process-creating wrappers (dotted names).

    Returns:
        The ScanResult, findings sorted by path and line.
    """
    files = collect_files(paths, excludes or [])
    roots = [(p if p.is_dir() else p.parent).resolve() for p in paths]
    project = Project(roots=roots)
    findings: list[Finding] = []
    parse_errors = 0
    apis = frozenset(process_apis or [])
    for path in files:
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            project.modules[path] = build_module(path, display_path(path), source, apis)
        except SyntaxError as error:
            parse_errors += 1
            findings.append(
                Finding(
                    rule="FA000",
                    name="parse-error",
                    severity="info",
                    path=display_path(path),
                    line=error.lineno or 1,
                    col=error.offset or 1,
                    end_line=error.lineno or 1,
                    message=f"could not parse: {error.msg}",
                    hint="fix the syntax error (or exclude the file)",
                    breaks_under=(),
                    motivation="scanner",
                )
            )
    project.link()
    for mod in project.modules.values():
        seen: set[tuple[str, int, int]] = set()
        for rule in RULES.values():
            for finding in rule(mod, project):
                key = (finding.rule, finding.line, finding.col)
                if key in seen or is_suppressed(mod, finding):
                    continue
                seen.add(key)
                findings.append(finding)
    findings.sort(key=_finding_order)
    return ScanResult(findings, len(files), parse_errors, project)


def _finding_order(finding: Finding) -> tuple[str, int, int, str]:
    """Sort key: path, line, column, rule.

    Args:
        finding: a finding.

    Returns:
        The key.
    """
    return (finding.path, finding.line, finding.col, finding.rule)
