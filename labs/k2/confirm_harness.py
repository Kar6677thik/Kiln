"""Lab K2 (b): how well is forkcheck calibrated? Precision/recall per rule, plus --confirm.

WHY THIS MODULE EXISTS
    A checker is only useful if its warnings are usually right (precision) and it rarely
    misses a real problem (recall). This harness scores every rule against the corpus, where
    the right answers are written down in advance: `# expect: FA00x` comments in the
    synthetic files, and expected.json in each real case. Then it runs each real case under
    fork, forkserver and spawn (`--confirm`) to check that the predicted breakage really
    happens (docs/concepts/static-vs-dynamic-analysis.md).

HOW IT FITS
    labs/k2/k2_lab.py calibrate ──▶ run_calibration()
        ├─ kiln.forkcheck.scan(corpus file's folder) ──▶ findings vs `# expect:` ──▶ TP/FP/FN
        ├─ corpus/real/*/expected.json must_fire / must_not_fire ──▶ TP/FP/FN
        └─ kiln.forkcheck.confirm.confirm_entry(entry) ──▶ outcome per start method
    One ExperimentRecord: results/K2/calibrate/<run_id>.json

READ FIRST
    docs/concepts/static-vs-dynamic-analysis.md, docs/labs/K2-forkcheck.md

MILESTONE
    Introduced in K2.
"""

import json
import re
from collections import Counter
from pathlib import Path

from kiln.bench import records
from kiln.forkcheck import scan
from kiln.forkcheck.confirm import confirm_entry

EXPECT = re.compile(r"#\s*expect:\s*((?:FA\d{3}[,\s]*)+)")
RULES = [f"FA00{n}" for n in range(1, 9)]


def expectations(path: Path) -> set[tuple[str, int, str]]:
    """The (file, line, rule) findings a synthetic file says must fire.

    Why:
        The right answer is written next to the code it's about, so a reader can check it.

    Args:
        path: a synthetic corpus file.

    Returns:
        The expected findings.
    """
    found = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = EXPECT.search(line)
        if match is not None:
            for rule in re.findall(r"FA\d{3}", match.group(1)):
                found.add((path.name, number, rule))
    return found


def score_synthetic(corpus: Path, tp: Counter[str], fp: Counter[str], fn: Counter[str]) -> None:
    """Count true positives, false positives and misses over every synthetic file.

    Why:
        Every finding is either expected (TP) or not (FP); every expectation is either found
        (TP) or not (FN). Exact lines are required, so "right rule, wrong place" counts twice:
        once as a false alarm and once as a miss.

    Args:
        corpus: the corpus/synthetic folder.
        tp: true positives per rule (updated).
        fp: false positives per rule (updated).
        fn: false negatives per rule (updated).
    """
    for path in sorted(corpus.glob("FA00*/*.py")):
        expected = expectations(path)
        result = scan([path.parent])
        actual = {
            (Path(f.path).name, f.line, f.rule)
            for f in result.findings
            if Path(f.path).name == path.name and f.rule in RULES
        }
        for item in actual & expected:
            tp[item[2]] += 1
        for item in actual - expected:
            fp[item[2]] += 1
        for item in expected - actual:
            fn[item[2]] += 1


def score_real(
    corpus: Path, tp: Counter[str], fp: Counter[str], fn: Counter[str]
) -> list[dict[str, object]]:
    """Score the real cases and run --confirm on each.

    Why:
        The real cases are the reason the checker exists (SC-5): each one is a bug that really
        happened. Confirmation shows the predicted breakage actually occurs.

    Args:
        corpus: the corpus/real folder.
        tp: true positives per rule (updated).
        fp: false positives per rule (updated: a must_not_fire rule that fired).
        fn: false negatives per rule (updated: a must_fire finding that didn't).

    Returns:
        One row per case for the lab report's confirmation table.
    """
    rows: list[dict[str, object]] = []
    for case in sorted(p.parent for p in corpus.glob("*/expected.json")):
        expected = json.loads((case / "expected.json").read_text(encoding="utf-8"))
        findings = scan([case]).findings
        found = {(Path(f.path).name, f.line, f.rule) for f in findings}
        hits = 0
        for item in expected["must_fire"]:
            key = (item["path"], item["line"], item["rule"])
            if key in found:
                tp[item["rule"]] += 1
                hits += 1
            else:
                fn[item["rule"]] += 1
        fired = Counter(f.rule for f in findings)
        for item in expected["must_not_fire"]:
            fp[item["rule"]] += fired[item["rule"]]
        confirmation = confirm_entry(case / expected["entry"], [], [case])
        rows.append(
            {
                "case": case.name,
                "must_fire": len(expected["must_fire"]),
                "found": hits,
                "confirmed": confirmation.differs,
                **{m: run.outcome for m, run in confirmation.runs.items()},
                "expected_behaviour": expected.get("behaviour", {}),
            }
        )
    return rows


def ratio(top: int, bottom: int) -> float | None:
    """Top / bottom, or None when bottom is 0 (no data: not "perfect").

    Why:
        A rule that never fired has no precision at all; writing 1.0 would be a lie.

    Args:
        top: numerator.
        bottom: denominator.

    Returns:
        The ratio rounded to 3 places, or None.
    """
    return round(top / bottom, 3) if bottom else None


def run_calibration(repo: Path, results: Path) -> None:
    """Score every rule, confirm every real case, write one record, print the tables.

    Why:
        One command (`dev.sh lab k2-calibrate`) regenerates both tables of lab report (b).

    Args:
        repo: the repository root.
        results: the results folder.
    """
    tp: Counter[str] = Counter()
    fp: Counter[str] = Counter()
    fn: Counter[str] = Counter()
    score_synthetic(repo / "corpus" / "synthetic", tp, fp, fn)
    synthetic = (Counter(tp), Counter(fp), Counter(fn))
    rows = score_real(repo / "corpus" / "real", tp, fp, fn)
    record = records.new_record("K2", "calibrate", "corpus", {"rules": RULES}, 0)
    print("\n  rule | TP | FP | FN | precision | recall   (synthetic + real)")
    for rule in RULES:
        precision = ratio(tp[rule], tp[rule] + fp[rule])
        recall = ratio(tp[rule], tp[rule] + fn[rule])
        print(f"  {rule} | {tp[rule]} | {fp[rule]} | {fn[rule]} | {precision} | {recall}")
        for name, value in (("precision", precision), ("recall", recall)):
            if value is not None:
                records.add_metric(record, f"{name}_{rule}", "ratio", value=value)
        records.add_metric(record, f"tp_{rule}", "count", value=tp[rule])
        records.add_metric(record, f"fp_{rule}", "count", value=fp[rule])
        records.add_metric(record, f"fn_{rule}", "count", value=fn[rule])
        records.add_metric(record, f"synthetic_tp_{rule}", "count", value=synthetic[0][rule])
        records.add_metric(record, f"synthetic_fp_{rule}", "count", value=synthetic[1][rule])
        records.add_metric(record, f"synthetic_fn_{rule}", "count", value=synthetic[2][rule])
    detected = sum(1 for row in rows if row["found"] == row["must_fire"])
    records.add_metric(record, "real_cases_detected", "count", value=detected)
    records.add_metric(
        record, "real_cases_confirmed", "count", value=sum(1 for r in rows if r["confirmed"])
    )
    record["notes"] = json.dumps(rows)
    path = records.write_record(record, results)
    print("\n  case | must_fire found | confirmed? | fork | forkserver | spawn")
    for row in rows:
        print(
            f"  {row['case']} | {row['found']}/{row['must_fire']} | {row['confirmed']} | "
            f"{row['fork']} | {row['forkserver']} | {row['spawn']}"
        )
    print(f"\n  record: {path}")
