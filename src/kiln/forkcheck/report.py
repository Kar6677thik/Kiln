"""Render forkcheck findings as text or JSON, and pick the exit code.

WHY THIS MODULE EXISTS
    A checker is only useful if people (text) and tools (JSON, CI) can read its output, and if
    its exit code says "something is wrong" in a way a shell script can test. The formats and
    codes are fixed by docs/milestones/K2-start-methods-forkcheck.md §5.8, and golden tests
    pin them, so keeping all rendering in one small module makes them easy to change on purpose
    and hard to change by accident.

HOW IT FITS
    forkcheck.main ──ScanResult──▶ render_text / render_json ──▶ stdout
                   ──findings + fail_on──▶ exit_code ──▶ 0 / 1

READ FIRST
    docs/python/json.md, docs/python/f-strings.md

MILESTONE
    Introduced in K2.
"""

import json
from collections.abc import Sequence

from kiln.forkcheck.rules import Finding

SEVERITIES = ("error", "warning", "info")
RANK = {"info": 0, "warning": 1, "error": 2}
TOOL_NAME = "kiln-forkcheck"
FORMAT_VERSION = 1


def format_finding(finding: Finding) -> str:
    """One finding as one line of text.

    Why:
        One finding per line, in the `path:line:col:` shape editors and CI tools already know how to
        turn into clickable links.

    Args:
        finding: the finding.

    Returns:
        `path:line:col: RULE [severity] name: message (breaks under: ...) — hint: ...`
    """
    breaks = ", ".join(finding.breaks_under) if finding.breaks_under else "none"
    text = (
        f"{finding.path}:{finding.line}:{finding.col}: {finding.rule} [{finding.severity}] "
        f"{finding.name}: {finding.message} (breaks under: {breaks}) — hint: {finding.hint}"
    )
    if finding.confirmation is not None:
        text += f" [confirmation: {_confirmation_text(finding.confirmation)}]"
    return text


def _confirmation_text(confirmation: dict[str, object]) -> str:
    """Summarise a confirmation dict for the text format.

    Args:
        confirmation: {"fork": ..., "forkserver": ..., "spawn": ..., "differs": bool}.

    Returns:
        e.g. "differs (fork=ok, forkserver=exception:PicklingError, spawn=exception:...)".
    """
    verdict = "differs" if confirmation.get("differs") else "same"
    methods = ", ".join(f"{m}={confirmation.get(m)}" for m in ("fork", "forkserver", "spawn"))
    return f"{verdict} ({methods})"


def summary_line(findings: Sequence[Finding], files_scanned: int, parse_errors: int) -> str:
    """The last line of the text report: counts per severity, files and parse errors.

    Args:
        findings: every finding reported.
        files_scanned: how many files were read.
        parse_errors: how many could not be parsed.

    Returns:
        e.g. "2 errors, 1 warning, 0 infos in 3 files (0 parse errors)".
    """
    counts = {s: sum(1 for f in findings if f.severity == s) for s in SEVERITIES}
    parts = [f"{counts[s]} {s}{'' if counts[s] == 1 else 's'}" for s in SEVERITIES]
    files = f"{files_scanned} file{'' if files_scanned == 1 else 's'}"
    return f"{', '.join(parts)} in {files} ({parse_errors} parse errors)"


def render_text(findings: Sequence[Finding], files_scanned: int, parse_errors: int) -> str:
    """The whole text report.

    Args:
        findings: sorted findings.
        files_scanned: how many files were read.
        parse_errors: how many could not be parsed.

    Returns:
        One line per finding, then the summary line, ending with a newline.
    """
    lines = [format_finding(f) for f in findings]
    lines.append(summary_line(findings, files_scanned, parse_errors))
    return "\n".join(lines) + "\n"


def finding_dict(finding: Finding) -> dict[str, object]:
    """A finding as the JSON object of §5.8 (keys in the documented order).

    Why:
        Spelling out each key (instead of dataclasses.asdict) pins the JSON field order and names
        that tools depend on (§5.8).

    Args:
        finding: the finding.

    Returns:
        A dict ready for json.dumps.
    """
    return {
        "rule": finding.rule,
        "name": finding.name,
        "severity": finding.severity,
        "path": finding.path,
        "line": finding.line,
        "col": finding.col,
        "end_line": finding.end_line,
        "message": finding.message,
        "hint": finding.hint,
        "breaks_under": list(finding.breaks_under),
        "motivation": finding.motivation,
        "confirmation": finding.confirmation,
    }


def render_json(findings: Sequence[Finding], files_scanned: int) -> str:
    """The whole JSON report.

    Why:
        Machines (CI, the calibration harness) need a stable format; `version` lets the format
        change later without breaking them silently.

    Args:
        findings: sorted findings.
        files_scanned: how many files were read.

    Returns:
        A JSON document followed by a newline.
    """
    document = {
        "tool": TOOL_NAME,
        "version": FORMAT_VERSION,
        "files_scanned": files_scanned,
        "findings": [finding_dict(f) for f in findings],
    }
    # WHY: ensure_ascii=False keeps the "—" and any non-English names readable in the output.
    return json.dumps(document, ensure_ascii=False) + "\n"


def exit_code(findings: Sequence[Finding], fail_on: str) -> int:
    """0 if no finding reaches `fail_on`, else 1 (§5.8). Parse errors (FA000) never count.

    Args:
        findings: every finding reported.
        fail_on: "error", "warning" or "info".

    Returns:
        0 or 1.
    """
    threshold = RANK[fail_on]
    failing = [f for f in findings if f.rule != "FA000" and RANK[f.severity] >= threshold]
    return 1 if failing else 0
