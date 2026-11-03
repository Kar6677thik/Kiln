"""check_docs.py: refuse to call work "done" while its explanations are missing.

WHY THIS MODULE EXISTS
    Kiln is a learning project for a reader who is new to Python: documentation is the
    product (ADR-001, ADR-026). Promises like "every function explains *why*" or "every
    primer has a quiz" are easy to forget under time pressure. This script turns those
    promises into checks that fail loudly, so `./scripts/dev.sh check` (and therefore every
    commit) enforces them. The rules come from docs/standards/documentation.md §10 and are
    specified precisely in docs/milestones/K0-foundations.md §5.9 (DOC001–DOC017).

HOW IT FITS
    dev.sh docs-check ──▶ main() ──▶ run every check_docNNN(root) ──▶ list[Problem] ──▶ report
    Each check is a separate function that only reads files below `root`. Tests run each
    check against a tiny good fixture and a tiny bad fixture (tests/fixtures/check_docs/).

READ FIRST
    docs/standards/documentation.md (the rules), docs/milestones/K0-foundations.md §5.9
    (the precise definition of each DOC id), docs/python/dataclasses.md,
    docs/python/comprehensions.md

MILESTONE
    Introduced in K0. Later milestones may add checks (for example K10's `--only` profiles).

Usage:
    python scripts/check_docs.py [--root DIR] [--only DOC001,DOC008] [--format text|json]
    Exit code: 0 = no problems, 1 = problems found, 2 = usage error.
"""

# PY: this script uses only the standard library, so it runs on any Python ≥ 3.13
# without installing anything (K0 spec §5.9).
import argparse
import ast
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# The four headings every module docstring must contain (documentation.md §4.1).
MODULE_HEADINGS = ("WHY THIS MODULE EXISTS", "HOW IT FITS", "READ FIRST", "MILESTONE")

# The headings of a concept primer (documentation.md §2), compared case-insensitively.
PRIMER_HEADINGS = (
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

# The headings of a Python note (documentation.md §3).
NOTE_HEADINGS = (
    "What it looks like",
    "What it means",
    "Why people use it",
    "How Kiln uses it",
    "Gotchas",
    "Try it",
    "Check yourself",
)

# The 13 headings of a lab report (documentation.md §6, templates/lab-report.md), without
# their "N." numbers.
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

# WHY: planning documents (specs, milestone plans, ADRs, templates, research) legitimately
# link to files that a *later* milestone creates. Learning documents (primers, notes, tours,
# labs, glossary, READMEs) must only link to things that exist. So a missing link target is
# tolerated only when the linking file is a planning document AND the target lives in one of
# these "future" areas.
PLANNING_DOCS = (
    "docs/spec/",
    "docs/milestones/",
    "docs/templates/",
    "docs/research/",
    "docs/standards/",
    "docs/decisions.md",
)
FUTURE_AREAS = (
    "src/",
    "labs/",
    "tests/",
    "corpus/",
    "results/",
    "rust/",
    "examples/",
    "docs/labs/",
    "docs/tours/",
    "docs/concepts/",
    "docs/python/",
    "docs/research/incidents.md",
    "docs/teach-back",
)

# WHY: a bold term is **text**, but the text may itself contain "**" inside backticks
# (for example **`**kwargs`**), so a backticked chunk is matched first as a whole.
BOLD_TERM = re.compile(r"\*\*((?:`[^`]*`|[^*`])+?)\*\*")

# WHY: this checker's own tests contain deliberately broken examples (tags pointing nowhere,
# links to files that don't exist) inside string literals. Scanning them for tags and doc
# mentions would report the examples themselves, so that one file is skipped by those scans.
SELF_TEST_FILES = ("tests/unit/test_check_docs.py",)

# Statuses in docs/milestones/README.md that mean "work has started" (DOC016).
STARTED_STATUSES = ("🚧", "👀", "✅")


# PY: @dataclass generates __init__/__repr__/__eq__ from the fields. frozen=True makes it
# read-only. See docs/python/dataclasses.md.
@dataclass(frozen=True)
class Problem:
    """One documentation problem, with where it is and how to fix it.

    Why:
        A uniform record lets every check report the same way, and lets the output be
        sorted, filtered (--only) and printed as text or JSON.
    """

    path: str
    line: int
    code: str
    message: str
    fix: str

    def render(self) -> str:
        """Format the problem as `path:line: DOCnnn message (fix: ...)`.

        Returns:
            One line of text.
        """
        return f"{self.path}:{self.line}: {self.code} {self.message} (fix: {self.fix})"


# ---------------------------------------------------------------------------------------------
# Helpers: finding files, parsing Python, parsing markdown
# ---------------------------------------------------------------------------------------------


def rel(root: Path, path: Path) -> str:
    """Return `path` relative to the repo root, with forward slashes.

    Why:
        Messages should name files the same way on every machine.

    Args:
        root: the repository root.
        path: a file below it.

    Returns:
        Something like "src/kiln/doctor.py".
    """
    return path.relative_to(root).as_posix()


def code_files(root: Path, include_tests: bool) -> list[Path]:
    """List the Python files the code checks look at.

    Why:
        documentation.md §10 defines the scope: src/, labs/, scripts/ (and tests/ for some
        checks). Documentation snippets are teaching code and are excluded.

    Args:
        root: the repository root.
        include_tests: whether to include tests/**/*.py.

    Returns:
        Sorted list of paths.
    """
    files: list[Path] = []
    files += (root / "src").rglob("*.py")
    files += (root / "labs").rglob("*.py")
    files += (root / "scripts").glob("*.py")
    if include_tests:
        # WHY: fixture trees for this very checker deliberately contain bad files.
        files += [p for p in (root / "tests").rglob("*.py") if "fixtures" not in p.parts]
    return sorted(files)


def parse_python(path: Path) -> ast.Module | None:
    """Parse a Python file into a syntax tree, or return None if it doesn't parse.

    Why:
        Every Python check reads code through `ast`, Python's own parser, so it understands
        code exactly the way Python does (not with fragile text searches).

    Args:
        path: the .py file.

    Returns:
        The parsed module, or None on a syntax error (ruff reports those separately).
    """
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return None


def is_empty_init(path: Path, tree: ast.Module) -> bool:
    """Return True for an `__init__.py` that contains no statements at all.

    Why:
        An empty `__init__.py` only marks a folder as a package; demanding a docstring there
        would be noise (K0 spec, DOC001).

    Args:
        path: the file.
        tree: its parsed syntax tree.

    Returns:
        Whether it's an empty package marker.
    """
    return path.name == "__init__.py" and not tree.body


def function_like_nodes(
    tree: ast.Module,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef]:
    """Return every def, async def and class anywhere in the tree (nested ones too).

    Why:
        documentation.md §4.2 requires a docstring on every function and class, private and
        nested ones included.

    Args:
        tree: a parsed module.

    Returns:
        Each function or class definition node, in tree-walk order.
    """
    nodes: list[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            nodes.append(node)
    return nodes


def body_line_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count the source lines a function body spans, not counting its docstring.

    Why:
        DOC004 asks for a `Why:` section only on functions longer than 5 lines.

    Args:
        node: a function definition.

    Returns:
        The number of source lines from the first real statement to the end.
    """
    statements = node.body
    if statements and ast.get_docstring(node) is not None:
        statements = statements[1:]
    if not statements:
        return 0
    first = statements[0].lineno
    last = max(getattr(s, "end_lineno", s.lineno) or s.lineno for s in statements)
    return last - first + 1


def strip_code_fences(text: str) -> str:
    """Blank out fenced code blocks (```...```) but keep the line count.

    Why:
        Links and headings inside code examples are not real links or headings. Keeping the
        newlines means line numbers in messages still match the file.

    Args:
        text: markdown text.

    Returns:
        The same text with code-block contents replaced by empty lines.
    """
    out_lines = []
    inside = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            inside = not inside
            out_lines.append("")
            continue
        out_lines.append("" if inside else line)
    return "\n".join(out_lines)


def github_slug(heading: str) -> str:
    """Turn a markdown heading into the anchor GitHub generates for it.

    Why:
        Links like `file.md#5-shutdown` must point at a real heading (DOC008). GitHub's rule:
        lowercase; drop everything except letters, digits, spaces, hyphens and underscores;
        turn spaces into hyphens.

    Args:
        heading: the heading text without the leading #'s.

    Returns:
        The anchor slug.

    Example:
        >>> github_slug("5. Shutdown: graceful & fast")
        '5-shutdown-graceful--fast'
    """
    text = heading.strip().lower()
    # WHY: inline-code backticks and emphasis markers disappear from the anchor.
    text = re.sub(r"[`*]", "", text)
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors_of(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    """Collect every anchor a markdown file offers: heading slugs and <a id="..."> tags.

    Why:
        Needed to verify `#anchor` parts of links. Cached because many links point at the
        same few spec files.

    Args:
        path: a markdown file.
        cache: shared cache between calls.

    Returns:
        The set of anchor names.
    """
    if path in cache:
        return cache[path]
    text = strip_code_fences(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    counts: dict[str, int] = {}
    for match in re.finditer(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.MULTILINE):
        slug = github_slug(match.group(1))
        # WHY: GitHub numbers repeated headings: "notes", "notes-1", "notes-2"...
        if slug in counts:
            counts[slug] += 1
            found.add(f"{slug}-{counts[slug]}")
        else:
            counts[slug] = 0
            found.add(slug)
    found.update(re.findall(r'<a id="([^"]+)"', text))
    cache[path] = found
    return found


def headings_of(text: str) -> list[str]:
    """Return the text of every markdown heading, with any leading "N." number removed.

    Why:
        Lab-report headings are numbered ("## 6. Results: before"); the required list isn't.

    Args:
        text: markdown text.

    Returns:
        Heading texts, in order.
    """
    found = []
    for match in re.finditer(r"^#{1,6}\s+(.+?)\s*$", strip_code_fences(text), re.MULTILINE):
        found.append(re.sub(r"^\d+\.\s*", "", match.group(1)).strip())
    return found


def line_of(text: str, index: int) -> int:
    """Convert a character position in a text into a 1-based line number.

    Why:
        Regex matches report character offsets; people want line numbers.

    Args:
        text: the whole text.
        index: a character offset into it.

    Returns:
        The line number.
    """
    return text.count("\n", 0, index) + 1


def docs_markdown(root: Path) -> list[Path]:
    """List the markdown files whose links DOC008 verifies.

    Why:
        documentation.md §10: every markdown file under docs/, plus README.md.

    Args:
        root: the repository root.

    Returns:
        Sorted list of paths that exist.
    """
    files = sorted((root / "docs").rglob("*.md"))
    files += [p for p in (root / "README.md",) if p.exists()]
    return files


def terms_introduced(text: str) -> list[str]:
    """Extract the bold terms from a document's "Terms introduced:" line.

    Why:
        DOC014 checks each of these terms has a glossary entry.

    Args:
        text: a primer or Python note.

    Returns:
        The terms, without the ** markers.
    """
    terms: list[str] = []
    for line in text.splitlines():
        if "Terms introduced" in line:
            # WHY: skip the label itself ("**Terms introduced:**"), keep the items after it.
            after = line.split("Terms introduced", 1)[1]
            terms += re.findall(BOLD_TERM, after.lstrip(":*").lstrip())
    return [t for t in terms if t.strip(":").strip()]


def normalize_term(term: str) -> str:
    """Normalize a term for comparison: lowercase, no backticks, no trailing period.

    Why:
        The glossary writes "**`gc.freeze()`.**" and a primer may write "**gc.freeze()**";
        they are the same term.

    Args:
        term: a raw term.

    Returns:
        The normalized key.
    """
    return term.replace("`", "").replace('"', "").strip().rstrip(".:").strip().lower()


# ---------------------------------------------------------------------------------------------
# The checks. Each takes the repo root and returns a list of Problems.
# ---------------------------------------------------------------------------------------------


def check_doc001(root: Path) -> list[Problem]:
    """DOC001: every code file (except tests and empty __init__.py) has a module docstring.

    Why:
        The module docstring is where a reader learns why a file exists before reading it.

    Args:
        root: the repository root.

    Returns:
        Problems found.
    """
    problems = []
    for path in code_files(root, include_tests=False):
        tree = parse_python(path)
        if tree is None or is_empty_init(path, tree):
            continue
        if ast.get_docstring(tree) is None:
            problems.append(
                Problem(
                    rel(root, path),
                    1,
                    "DOC001",
                    "module has no docstring",
                    "add a module docstring (documentation.md §4.1)",
                )
            )
    return problems


def check_doc002(root: Path) -> list[Problem]:
    """DOC002: module docstrings contain the four required headings.

    Why:
        WHY / HOW IT FITS / READ FIRST / MILESTONE give every file the same orientation.

    Args:
        root: the repository root.

    Returns:
        Problems found.
    """
    problems = []
    for path in code_files(root, include_tests=False):
        tree = parse_python(path)
        if tree is None or is_empty_init(path, tree):
            continue
        docstring = ast.get_docstring(tree)
        if docstring is None:
            continue  # WHY: DOC001 already reports this file.
        lines = {line.strip() for line in docstring.splitlines()}
        missing = [heading for heading in MODULE_HEADINGS if heading not in lines]
        if missing:
            problems.append(
                Problem(
                    rel(root, path),
                    1,
                    "DOC002",
                    "module docstring lacks: " + ", ".join(missing),
                    "add each heading on its own line (documentation.md §4.1)",
                )
            )
    return problems


def check_doc003(root: Path) -> list[Problem]:
    """DOC003: every function and class in src/, labs/ and scripts/ has a docstring.

    Why:
        documentation.md §4.2: private and nested functions too. The reader can't ask the
        author; the docstring is the author's answer.

    Args:
        root: the repository root.

    Returns:
        Problems found.
    """
    problems = []
    for path in code_files(root, include_tests=False):
        tree = parse_python(path)
        if tree is None:
            continue
        for node in function_like_nodes(tree):
            if ast.get_docstring(node) is None:
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                problems.append(
                    Problem(
                        rel(root, path),
                        node.lineno,
                        "DOC003",
                        f"{kind} {node.name!r} has no docstring",
                        "add a docstring (documentation.md §4.2)",
                    )
                )
    return problems


def check_doc004(root: Path) -> list[Problem]:
    """DOC004: classes, and functions longer than 5 lines, have a `Why:` section.

    Why:
        "Why before how" (documentation.md §0): the reason a piece of code exists is the part
        the code itself can never show.

    Args:
        root: the repository root.

    Returns:
        Problems found.
    """
    problems = []
    for path in code_files(root, include_tests=False):
        tree = parse_python(path)
        if tree is None:
            continue
        for node in function_like_nodes(tree):
            docstring = ast.get_docstring(node)
            if docstring is None:
                continue  # WHY: DOC003 reports missing docstrings.
            needs_why = isinstance(node, ast.ClassDef) or body_line_count(node) > 5
            has_why = any(line.strip().startswith("Why:") for line in docstring.splitlines())
            if needs_why and not has_why:
                problems.append(
                    Problem(
                        rel(root, path),
                        node.lineno,
                        "DOC004",
                        f"{node.name!r} docstring has no 'Why:' section",
                        "add 'Why:' explaining the reason it exists (documentation.md §4.2)",
                    )
                )
    return problems


def constructs_in(tree: ast.Module) -> dict[str, int]:
    """Find which Python language features a file uses, and the first line of each.

    Why:
        DOC005: every feature the reader will meet must have a Python note. This is the
        "construct map" of K0 spec §5.9: AST node type → ledger key.

    Args:
        tree: a parsed module.

    Returns:
        A dict {ledger key: first line number where it appears}.
    """
    found: dict[str, int] = {}
    # WHY: a nested def is a def whose parent is a def. Collect those first.
    nested: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for child in ast.walk(node):
                if child is not node and isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    nested.add(id(child))

    for node in ast.walk(tree):
        _classify_node(node, found, nested)
    return found


def _mark(found: dict[str, int], key: str, node: ast.AST) -> None:
    """Remember the first line where a construct appears.

    Why:
        The ledger's "first used in" column wants the earliest use, and ast.walk() does not
        visit nodes in line order, so we keep the smallest line seen so far.

    Args:
        found: the {ledger key: first line} dict being filled in.
        key: the ledger key.
        node: the AST node where it was seen.
    """
    line = getattr(node, "lineno", 1)
    if key not in found or line < found[key]:
        found[key] = line


def _base_names(node: ast.ClassDef) -> list[str]:
    """Return the simple names of a class's base classes (`Enum`, `Protocol`, ...).

    Why:
        A base can be written `Enum` or `enum.Enum`; both must count as "uses enum".

    Args:
        node: a class definition.

    Returns:
        The last part of each base's name ("enum.Enum" → "Enum").
    """
    names = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> list[str]:
    """Return the simple names of a definition's decorators (`dataclass`, `parametrize`...).

    Why:
        `@dataclass`, `@dataclass(frozen=True)` and `@dataclasses.dataclass` are the same
        decorator written three ways; this reduces each to its plain name.

    Args:
        node: a def or class.

    Returns:
        The last part of each decorator's name, with call arguments ignored.
    """
    names = []
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, ast.Attribute):
            names.append(target.attr)
    return names


def _classify_node(node: ast.AST, found: dict[str, int], nested: set[int]) -> None:
    """Record every construct-map key that one AST node represents.

    Why:
        Kept separate from constructs_in() so each function stays readable. This function is
        the construct map of K0 spec §5.9, written as code.

    Args:
        node: one node of the syntax tree.
        found: the {ledger key: first line} dict to update.
        nested: ids of function nodes that are nested inside another function.
    """
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        _mark(found, "functions", node)
        if node.name.startswith("__") and node.name.endswith("__"):
            _mark(found, "dunder-methods", node)
        if id(node) in nested:
            _mark(found, "closures", node)
        if node.args.vararg is not None or node.args.kwarg is not None:
            _mark(found, "star-args-and-unpacking", node)
        if node.returns is not None or any(a.annotation for a in node.args.args):
            _mark(found, "type-hints", node)
        if getattr(node, "type_params", None):
            _mark(found, "generics", node)
    if isinstance(node, ast.AsyncFunctionDef | ast.Await):
        _mark(found, "async-await", node)
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        decorators = _decorator_names(node)
        if decorators:
            _mark(found, "decorators", node)
        if "dataclass" in decorators:
            _mark(found, "dataclasses", node)
    if isinstance(node, ast.ClassDef):
        _mark(found, "classes-and-objects", node)
        bases = _base_names(node)
        if {"Enum", "IntEnum", "StrEnum"} & set(bases):
            _mark(found, "enum", node)
        if "Protocol" in bases:
            _mark(found, "protocols", node)
        if getattr(node, "type_params", None):
            _mark(found, "generics", node)
    if isinstance(node, ast.With | ast.AsyncWith):
        _mark(found, "context-managers", node)
    if isinstance(node, ast.Try | ast.Raise):
        _mark(found, "exceptions", node)
    if isinstance(node, ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp):
        _mark(found, "comprehensions", node)
    if isinstance(node, ast.JoinedStr):
        _mark(found, "f-strings", node)
    # WHY: TemplateStr (t-strings) only exists in the ast module on Python 3.14+.
    template_type = getattr(ast, "TemplateStr", None)
    if template_type is not None and isinstance(node, template_type):
        _mark(found, "t-strings", node)
    if isinstance(node, ast.AnnAssign):
        _mark(found, "type-hints", node)
    if isinstance(node, ast.Import | ast.ImportFrom):
        _mark(found, "modules-and-imports", node)
    if isinstance(node, ast.Yield | ast.YieldFrom):
        _mark(found, "generators", node)
    if isinstance(node, ast.Lambda):
        _mark(found, "lambda", node)
    if isinstance(node, ast.NamedExpr):
        _mark(found, "walrus", node)
    if isinstance(node, ast.Match):
        _mark(found, "match-statement", node)
    if isinstance(node, ast.Global | ast.Nonlocal):
        _mark(found, "global-and-nonlocal", node)
    if isinstance(node, ast.Starred):
        _mark(found, "star-args-and-unpacking", node)
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
        _mark(found, "slicing", node)
    if isinstance(node, ast.Assert):
        _mark(found, "assert", node)
    if isinstance(node, ast.Delete):
        _mark(found, "del", node)
    if isinstance(node, ast.TypeAlias):
        _mark(found, "generics", node)
    _classify_slots(node, found)


def _classify_slots(node: ast.AST, found: dict[str, int]) -> None:
    """Mark `__slots__ = ...` assignments and `slots=True` keyword arguments.

    Why:
        Both spellings mean "this class uses slots" (K0 spec §5.9 construct map), and they
        look completely different in the syntax tree, so each needs its own test.

    Args:
        node: one AST node.
        found: the {ledger key: first line} dict to update.
    """
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__slots__":
                _mark(found, "slots", node)
    is_slots_keyword = isinstance(node, ast.keyword) and node.arg == "slots"
    if is_slots_keyword and isinstance(node.value, ast.Constant) and node.value.value is True:
        _mark(found, "slots", node)


def ledger_keys(root: Path) -> set[str]:
    """Read the feature keys listed in the Python ledger (docs/python/README.md).

    Why:
        DOC005 compares the constructs used in code with the features the ledger documents.

    Args:
        root: the repository root.

    Returns:
        The set of keys in the first column of the ledger table.
    """
    path = root / "docs" / "python" / "README.md"
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8")
    # WHY: only the table under the "ledger" heading counts, not the reading-order table.
    section = text.split("The ledger", 1)[1] if "The ledger" in text else ""
    keys = set()
    for line in section.splitlines():
        if line.startswith("|") and not line.startswith("|---"):
            first_cell = line.strip("|").split("|")[0].strip().strip("`")
            if first_cell and first_cell.lower() != "feature":
                keys.add(first_cell)
    return keys


def check_doc005(root: Path) -> list[Problem]:
    """DOC005: every language construct used in code has a row in the Python ledger.

    Why:
        The reader is new to Python (ADR-002). If code uses a feature with no note, the
        reader hits a wall. The ledger records where each feature is first used and which
        note explains it.

    Args:
        root: the repository root.

    Returns:
        Problems found (one per missing construct, at its first use).
    """
    documented = ledger_keys(root)
    first_use: dict[str, tuple[str, int]] = {}
    for path in code_files(root, include_tests=True):
        tree = parse_python(path)
        if tree is None:
            continue
        for key, line in constructs_in(tree).items():
            if key not in first_use:
                first_use[key] = (rel(root, path), line)
    problems = []
    for key, (path, line) in sorted(first_use.items()):
        if key not in documented:
            problems.append(
                Problem(
                    path,
                    line,
                    "DOC005",
                    f"construct {key!r} is used but has no row in docs/python/README.md",
                    f"write docs/python/{key}.md (or link an existing note) and add a ledger row",
                )
            )
    return problems


def tags_in_code(root: Path, tag: str) -> list[tuple[str, int, str]]:
    """Find `# TAG(value):` comment tags in code files.

    Why:
        Shared by DOC006 (CONCEPT) and DOC007 (INCIDENT).

    Args:
        root: the repository root.
        tag: "CONCEPT" or "INCIDENT".

    Returns:
        (path, line, value) for each tag found.
    """
    pattern = re.compile(rf"#\s*{tag}\(([^)]+)\):")
    found = []
    for path in code_files(root, include_tests=True):
        if rel(root, path) in SELF_TEST_FILES:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for match in pattern.finditer(line):
                found.append((rel(root, path), number, match.group(1).strip()))
    return found


def check_doc006(root: Path) -> list[Problem]:
    """DOC006: every CONCEPT tag in a code comment points at an existing primer.

    Why:
        The tag promises "the explanation is in docs/concepts/<slug>.md"; a broken promise
        strands the reader.

    Args:
        root: the repository root.

    Returns:
        Problems found.
    """
    problems = []
    for path, line, slug in tags_in_code(root, "CONCEPT"):
        if not (root / "docs" / "concepts" / f"{slug}.md").exists():
            problems.append(
                Problem(
                    path,
                    line,
                    "DOC006",
                    f"CONCEPT({slug}) has no docs/concepts/{slug}.md",
                    "write the primer or fix the slug",
                )
            )
    return problems


def check_doc007(root: Path) -> list[Problem]:
    """DOC007: every INCIDENT tag in a code comment names a known incident.

    Why:
        Incident tags link code to the real-world story it reproduces (documentation.md §4.3).

    Args:
        root: the repository root.

    Returns:
        Problems found.
    """
    registry = root / "docs" / "research" / "incidents.md"
    known = anchors_of(registry, {}) if registry.exists() else set()
    problems = []
    for path, line, incident in tags_in_code(root, "INCIDENT"):
        if incident not in known:
            problems.append(
                Problem(
                    path,
                    line,
                    "DOC007",
                    f"INCIDENT({incident}) is not in incidents.md",
                    "add a '## <id>' section to docs/research/incidents.md or fix the id",
                )
            )
    return problems


def _link_problem(root: Path, source: Path, target: str, cache: dict[Path, set[str]]) -> str | None:
    """Decide whether one relative link from `source` is broken.

    Why:
        Shared logic for links in markdown and doc paths in code comments.

    Args:
        root: the repository root.
        source: the file containing the link.
        target: the link target, possibly with a #anchor.
        cache: shared anchor cache (see anchors_of).

    Returns:
        A description of the problem, or None if the link is fine.
    """
    path_part, _, anchor = target.partition("#")
    destination = (source.parent / path_part).resolve() if path_part else source.resolve()
    if not destination.exists():
        try:
            relative = destination.relative_to(root.resolve()).as_posix()
        except ValueError:
            return f"link {target!r} points outside the repository"
        source_rel = rel(root, source)
        planning = source_rel.startswith(PLANNING_DOCS)
        if planning and relative.startswith(FUTURE_AREAS):
            return None
        return f"link target {target!r} does not exist"
    if anchor and destination.suffix == ".md" and anchor not in anchors_of(destination, cache):
        return f"anchor #{anchor} not found in {path_part or source.name}"
    return None


def check_doc008(root: Path) -> list[Problem]:
    """DOC008: relative links in docs, and docs/ paths mentioned in code, resolve.

    Why:
        A broken link is a dead end for a learner following the reading path. Anchors are
        checked too, because "see §5" links break silently when headings change.

    Args:
        root: the repository root.

    Returns:
        Problems found.
    """
    problems = []
    cache: dict[Path, set[str]] = {}
    link = re.compile(r"\]\(([^)\s]+)\)")
    for path in docs_markdown(root):
        text = strip_code_fences(path.read_text(encoding="utf-8"))
        for match in link.finditer(text):
            target = match.group(1)
            # WHY: skip web links, e-mail links and template placeholders like <slug>.
            if target.startswith(("http://", "https://", "mailto:")) or "<" in target:
                continue
            problem = _link_problem(root, path, target, cache)
            if problem:
                problems.append(
                    Problem(
                        rel(root, path),
                        line_of(text, match.start()),
                        "DOC008",
                        problem,
                        "fix the path or anchor",
                    )
                )
    mention = re.compile(r"(docs/[\w./-]+\.md)(#[\w-]+)?")
    for path in code_files(root, include_tests=True):
        if rel(root, path) in SELF_TEST_FILES:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for match in mention.finditer(line):
                if not (root / match.group(1)).exists():
                    problems.append(
                        Problem(
                            rel(root, path),
                            number,
                            "DOC008",
                            f"mentions {match.group(1)}, which does not exist",
                            "fix the path in the comment or docstring",
                        )
                    )
    return problems


def _markdown_files(folder: Path) -> list[Path]:
    """List the .md files of a docs folder, excluding its README index.

    Args:
        folder: such as docs/concepts.

    Returns:
        Sorted list of paths (empty if the folder doesn't exist).
    """
    if not folder.exists():
        return []
    return sorted(p for p in folder.glob("*.md") if p.name != "README.md")


def _missing_headings(text: str, required: tuple[str, ...]) -> list[str]:
    """Return the required headings that a document lacks (case-insensitive).

    Args:
        text: the markdown document.
        required: the headings it must have.

    Returns:
        The missing ones, in required order.
    """
    present = {h.lower() for h in headings_of(text)}
    return [h for h in required if h.lower() not in present]


def check_doc009(root: Path) -> list[Problem]:
    """DOC009: every concept primer has the required sections and a "Terms introduced" line.

    Why:
        The primer structure (documentation.md §2) walks a beginner from "why care" to "check
        yourself"; a missing section is a missing step.

    Args:
        root: the repository root.

    Returns:
        Problems found.
    """
    problems = []
    for path in _markdown_files(root / "docs" / "concepts"):
        text = path.read_text(encoding="utf-8")
        missing = _missing_headings(text, PRIMER_HEADINGS)
        if "Terms introduced" not in text:
            missing.append("Terms introduced (header line)")
        if missing:
            problems.append(
                Problem(
                    rel(root, path),
                    1,
                    "DOC009",
                    "primer lacks: " + ", ".join(missing),
                    "follow docs/templates/concept-primer.md",
                )
            )
    return problems


def check_doc010(root: Path) -> list[Problem]:
    """DOC010: every concept primer has at least 3 hidden quiz answers (<details> blocks).

    Why:
        Self-testing is part of how the learner learns (documentation.md §2, item 8).

    Args:
        root: the repository root.

    Returns:
        Problems found.
    """
    problems = []
    for path in _markdown_files(root / "docs" / "concepts"):
        count = path.read_text(encoding="utf-8").count("<details>")
        if count < 3:
            problems.append(
                Problem(
                    rel(root, path),
                    1,
                    "DOC010",
                    f"only {count} <details> answers (need ≥ 3)",
                    "add Check-yourself questions with hidden answers",
                )
            )
    return problems


def check_doc011(root: Path) -> list[Problem]:
    """DOC011: every snippet a primer mentions exists.

    Why:
        "See it for yourself" is only honest if the reader can actually run the snippet.

    Args:
        root: the repository root.

    Returns:
        Problems found.
    """
    problems = []
    pattern = re.compile(r"(docs/concepts/snippets/[\w./-]+\.(?:py|sh))")
    for path in _markdown_files(root / "docs" / "concepts"):
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            if not (root / match.group(1)).exists():
                problems.append(
                    Problem(
                        rel(root, path),
                        line_of(text, match.start()),
                        "DOC011",
                        f"snippet {match.group(1)} does not exist",
                        "create it or fix the path",
                    )
                )
    return problems


def _lab_reports(root: Path) -> list[Path]:
    """List the lab reports: docs/labs/K*.md.

    Args:
        root: the repository root.

    Returns:
        Sorted paths.
    """
    folder = root / "docs" / "labs"
    return sorted(folder.glob("K*.md")) if folder.exists() else []


def check_doc012(root: Path) -> list[Problem]:
    """DOC012: every lab report has all 13 required sections.

    Why:
        The structure (hypothesis → numbers → conclusion → reference comparison) is what
        turns a measurement into a lesson (documentation.md §6).

    Args:
        root: the repository root.

    Returns:
        Problems found.
    """
    problems = []
    for path in _lab_reports(root):
        missing = _missing_headings(path.read_text(encoding="utf-8"), LAB_HEADINGS)
        if missing:
            problems.append(
                Problem(
                    rel(root, path),
                    1,
                    "DOC012",
                    "lab report lacks: " + ", ".join(missing),
                    "follow docs/templates/lab-report.md",
                )
            )
    return problems


def check_doc013(root: Path) -> list[Problem]:
    """DOC013: result tables in lab reports have no empty cells.

    Why:
        "Numbers are never invented" (documentation.md §6). An empty cell hides whether a
        value was forgotten or not measured; the rule is to write `not measured (<reason>)`.

    Args:
        root: the repository root.

    Returns:
        Problems found.
    """
    problems = []
    for path in _lab_reports(root):
        in_results = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.startswith("#"):
                in_results = "results" in line.lower()
                continue
            is_row = line.startswith("|") and not re.match(r"^\|[\s:|-]+\|?$", line)
            if in_results and is_row:
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                if any(cell == "" for cell in cells):
                    problems.append(
                        Problem(
                            rel(root, path),
                            number,
                            "DOC013",
                            "empty cell in a results table",
                            "fill it in, or write 'not measured (<reason>)'",
                        )
                    )
    return problems


def glossary_terms(root: Path) -> set[str]:
    """Read every defined term from docs/glossary.md (the bold text starting an entry).

    Why:
        DOC014 needs the set of terms the glossary defines, including entries that define
        several related terms at once ("PSS / RSS / USS").

    Args:
        root: the repository root.

    Returns:
        Normalized terms.
    """
    path = root / "docs" / "glossary.md"
    if not path.exists():
        return set()
    terms = set()
    entry = re.compile(r"^- \*\*((?:`[^`]*`|[^*`])+?)\*\*", re.MULTILINE)
    for match in entry.finditer(path.read_text(encoding="utf-8")):
        raw = match.group(1)
        terms.add(normalize_term(raw))
        # WHY: entries like "**PSS / RSS / USS.**" or "**Counter / gauge / histogram.**" define
        # several terms at once; accept each part, and a parenthesised short form too.
        for part in re.split(r"\s*/\s*", raw):
            terms.add(normalize_term(part))
            terms.add(normalize_term(re.sub(r"\s*\(.*?\)", "", part)))
            for inner in re.findall(r"\((.+?)\)", part):
                terms.add(normalize_term(inner))
    return terms


def term_variants(term: str) -> set[str]:
    """List the spellings under which a term may appear in the glossary.

    Why:
        A note may introduce "**file descriptor (fd)**" while the glossary entry is
        "**fd (file descriptor).**". Both name the same thing, so the term counts as defined
        when any of its variants is: the full text, the text without the parenthesis, the
        parenthesis contents, or any "/"-separated part.

    Args:
        term: the term as written in a "Terms introduced" line.

    Returns:
        The normalized variants.

    Example:
        >>> sorted(term_variants("file descriptor (fd)"))
        ['fd', 'file descriptor', 'file descriptor (fd)']
    """
    variants = {normalize_term(term)}
    for part in re.split(r"\s*/\s*", term):
        variants.add(normalize_term(part))
        variants.add(normalize_term(re.sub(r"\s*\(.*?\)", "", part)))
        for inner in re.findall(r"\((.+?)\)", part):
            variants.add(normalize_term(inner))
    return {variant for variant in variants if variant}


def check_doc014(root: Path) -> list[Problem]:
    """DOC014: every "Terms introduced" term of a primer or Python note is in the glossary.

    Why:
        The glossary is the reader's safety net: any bold new word must be look-up-able.

    Args:
        root: the repository root.

    Returns:
        Problems found.
    """
    known = glossary_terms(root)
    problems = []
    folders = (root / "docs" / "concepts", root / "docs" / "python")
    for folder in folders:
        for path in _markdown_files(folder):
            for term in terms_introduced(path.read_text(encoding="utf-8")):
                if not term_variants(term) & known:
                    problems.append(
                        Problem(
                            rel(root, path),
                            1,
                            "DOC014",
                            f"term {term!r} is not in docs/glossary.md",
                            "add a glossary entry: - **Term.** definition",
                        )
                    )
    return problems


def check_doc015(root: Path) -> list[Problem]:
    """DOC015: every test function has a docstring saying what behaviour it pins down.

    Why:
        Tests are documentation too (documentation.md §4.5): the docstring says what must
        stay true and why that matters.

    Args:
        root: the repository root.

    Returns:
        Problems found.
    """
    problems = []
    tests = root / "tests"
    files = (
        sorted(p for p in tests.rglob("test_*.py") if "fixtures" not in p.parts)
        if tests.exists()
        else []
    )
    for path in files:
        tree = parse_python(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            is_test = isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            if is_test and node.name.startswith("test_") and ast.get_docstring(node) is None:
                problems.append(
                    Problem(
                        rel(root, path),
                        node.lineno,
                        "DOC015",
                        f"test {node.name!r} has no docstring",
                        "say what behaviour it pins down and why (documentation.md §4.5)",
                    )
                )
    return problems


def started_milestones(root: Path) -> list[str]:
    """Return the milestones whose status in docs/milestones/README.md is 🚧, 👀 or ✅.

    Why:
        DOC016 only checks milestones that have started; 📝 ones are future work.

    Args:
        root: the repository root.

    Returns:
        Milestone ids such as ["K0"].
    """
    path = root / "docs" / "milestones" / "README.md"
    if not path.exists():
        return []
    started = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\|\s*(K\d+)\s*\|", line)
        if match and any(status in line for status in STARTED_STATUSES):
            started.append(match.group(1))
    return started


def deliverable_paths(spec_text: str) -> list[str]:
    """Extract the backticked paths from a milestone spec's "Docs deliverables" table.

    Why:
        The table is the milestone's promise of what learning material it produces.

    Args:
        spec_text: the milestone spec.

    Returns:
        Paths such as the docs/concepts primers listed in the table.
    """
    if "Docs deliverables" not in spec_text:
        return []
    section = spec_text.split("Docs deliverables", 1)[1]
    # WHY: the section ends at the next "## " heading.
    section = re.split(r"\n## ", section, maxsplit=1)[0]
    paths = []
    for line in section.splitlines():
        if line.startswith("|"):
            cells = line.strip("|").split("|")
            if len(cells) >= 2:
                paths += re.findall(r"`([\w./-]+\.(?:md|py|sh|txt))`", cells[1])
    return paths


def check_doc016(root: Path) -> list[Problem]:
    """DOC016: started milestones have every file listed in their "Docs deliverables".

    Why:
        A milestone isn't done until its learning material exists (ADR-026). Milestones that
        are only 📝 specified are skipped, so future work doesn't fail today's check.

    Args:
        root: the repository root.

    Returns:
        Problems found.
    """
    problems = []
    for milestone in started_milestones(root):
        specs = sorted((root / "docs" / "milestones").glob(f"{milestone}-*.md"))
        for spec in specs:
            for path in deliverable_paths(spec.read_text(encoding="utf-8")):
                if not (root / path).exists():
                    problems.append(
                        Problem(
                            rel(root, spec),
                            1,
                            "DOC016",
                            f"{milestone} deliverable {path} does not exist",
                            "write it (milestone-workflow.md §4)",
                        )
                    )
    return problems


def check_doc017(root: Path) -> list[Problem]:
    """DOC017: every Python note has the required sections.

    Why:
        The note structure (documentation.md §3) goes from a tiny example to "try it" and a
        quiz; skipping a section skips a learning step.

    Args:
        root: the repository root.

    Returns:
        Problems found.
    """
    problems = []
    for path in _markdown_files(root / "docs" / "python"):
        text = path.read_text(encoding="utf-8")
        missing = _missing_headings(text, NOTE_HEADINGS)
        if "Terms introduced" not in text:
            missing.append("Terms introduced (header line)")
        if missing:
            problems.append(
                Problem(
                    rel(root, path),
                    1,
                    "DOC017",
                    "Python note lacks: " + ", ".join(missing),
                    "follow docs/templates/python-note.md",
                )
            )
    return problems


# WHY: an explicit table from id to check function: easy to read, and it powers --only.
CHECKS: dict[str, Callable[[Path], list[Problem]]] = {
    "DOC001": check_doc001,
    "DOC002": check_doc002,
    "DOC003": check_doc003,
    "DOC004": check_doc004,
    "DOC005": check_doc005,
    "DOC006": check_doc006,
    "DOC007": check_doc007,
    "DOC008": check_doc008,
    "DOC009": check_doc009,
    "DOC010": check_doc010,
    "DOC011": check_doc011,
    "DOC012": check_doc012,
    "DOC013": check_doc013,
    "DOC014": check_doc014,
    "DOC015": check_doc015,
    "DOC016": check_doc016,
    "DOC017": check_doc017,
}


def problem_sort_key(problem: Problem) -> tuple[str, int, str]:
    """Sort problems by file, then line, then check id.

    Args:
        problem: one problem.

    Returns:
        The sort key.
    """
    return (problem.path, problem.line, problem.code)


def run(root: Path, only: list[str] | None = None) -> list[Problem]:
    """Run the selected checks (all by default) and return every problem, sorted.

    Why:
        The single function behind the CLI and the tests.

    Args:
        root: the repository root to check.
        only: check ids to run, or None for all.

    Returns:
        Problems sorted by file, line and code.
    """
    selected = only if only else list(CHECKS)
    problems: list[Problem] = []
    for check_id in selected:
        problems += CHECKS[check_id](root)
    return sorted(problems, key=problem_sort_key)


def main(argv: list[str]) -> int:
    """Parse arguments, run the checks, print the report.

    Why:
        Keeps command-line handling separate from the checks themselves.

    Args:
        argv: arguments after the script name.

    Returns:
        0 if clean, 1 if problems were found, 2 on a usage error.
    """
    parser = argparse.ArgumentParser(description="Check Kiln's documentation rules.")
    default_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--root", type=Path, default=default_root, help="repository root")
    parser.add_argument("--only", help="comma-separated check ids, e.g. DOC001,DOC008")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    only = [part.strip().upper() for part in args.only.split(",")] if args.only else None
    if only:
        unknown = [check_id for check_id in only if check_id not in CHECKS]
        if unknown:
            print(f"check_docs: unknown check id(s): {', '.join(unknown)}", file=sys.stderr)
            return 2
    problems = run(args.root.resolve(), only)
    if args.format == "json":
        print(json.dumps([problem.__dict__ for problem in problems], indent=2, ensure_ascii=False))
    else:
        for problem in problems:
            print(problem.render())
        checked = len(only) if only else len(CHECKS)
        print(f"check_docs: {len(problems)} problem(s) from {checked} check(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
