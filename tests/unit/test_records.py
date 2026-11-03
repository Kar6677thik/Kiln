"""Tests for the ExperimentRecord format (docs/spec/10-measurement-methodology.md §2)."""

import json
from pathlib import Path

import pytest

from kiln.bench import records


def make_valid_record() -> dict[str, object]:
    """Return a small, valid record with one single-value and one samples metric.

    Returns:
        A record that passes validate().
    """
    record = records.new_record("K0", "demo-experiment", "baseline", {"workers": 4}, 0)
    records.add_metric(record, "throughput", "jobs/s", value=1234.5)
    records.add_metric(record, "latency", "ms", samples=[0.5, 0.7, 1.1])
    return record


def test_new_record_fills_every_required_field() -> None:
    """A fresh record already has every required field, so labs only add what they measured."""
    record = records.new_record("K0", "demo", "v", {}, 0)
    assert records.validate(record) == []
    for key in records.REQUIRED_FIELDS:
        assert key in record


def test_run_id_names_the_interpreter() -> None:
    """The run id embeds the interpreter tag, so file names alone tell versions apart."""
    record = records.new_record("K0", "demo", "v", {}, 0)
    assert f"-py{records.interpreter_tag()}-" in str(record["run_id"])


def test_write_then_load_round_trips(tmp_path: Path) -> None:
    """write_record → load_records gives back exactly the same data (AC-0.7)."""
    record = make_valid_record()
    path = records.write_record(record, tmp_path)
    assert path == tmp_path / "K0" / "demo-experiment" / f"{record['run_id']}.json"
    assert records.load_records(path) == [record]
    assert records.load_records(tmp_path) == [record]


def test_write_is_atomic_and_leaves_no_temp_file(tmp_path: Path) -> None:
    """After writing, only the final .json exists: no half-written .tmp file is left behind."""
    path = records.write_record(make_valid_record(), tmp_path)
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


def test_missing_required_field_is_reported() -> None:
    """validate() names a missing required field (AC-0.7)."""
    record = make_valid_record()
    record.pop("variant")
    assert "missing required field 'variant'" in records.validate(record)


def test_metric_with_both_value_and_samples_is_rejected() -> None:
    """A metric must have exactly one of value or samples (AC-0.7, spec/10 §2.1)."""
    record = make_valid_record()
    record["metrics"] = {"x": {"unit": "ms", "value": 1, "samples": [1]}}
    assert any("exactly one" in problem for problem in records.validate(record))


def test_unknown_unit_is_rejected() -> None:
    """Units like "MB" are refused; only the spec/10 list (bytes, ms, jobs/s...) is allowed."""
    record = make_valid_record()
    record["metrics"] = {"x": {"unit": "MB", "value": 1}}
    assert any("unit" in problem for problem in records.validate(record))
    with pytest.raises(ValueError, match="unknown unit"):
        records.add_metric(make_valid_record(), "y", "MB", value=1)


def test_add_metric_requires_exactly_one_of_value_or_samples() -> None:
    """add_metric() refuses both or neither of value/samples, at the moment of the mistake."""
    with pytest.raises(ValueError, match="exactly one"):
        records.add_metric(make_valid_record(), "y", "ms")
    with pytest.raises(ValueError, match="exactly one"):
        records.add_metric(make_valid_record(), "y", "ms", value=1, samples=[1])


def test_booleans_are_not_measurements() -> None:
    """True is technically an int in Python, but a metric value of True is a bug; reject it."""
    record = make_valid_record()
    record["metrics"] = {"x": {"unit": "count", "value": True}}
    assert any("number" in problem for problem in records.validate(record))


def test_invalid_record_is_never_written(tmp_path: Path) -> None:
    """write_record() validates first and writes nothing if the record is invalid."""
    record = make_valid_record()
    record.pop("milestone")
    with pytest.raises(ValueError, match="invalid ExperimentRecord"):
        records.write_record(record, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_nan_is_refused(tmp_path: Path) -> None:
    """NaN isn't valid JSON; a failed measurement must be omitted and explained instead."""
    record = make_valid_record()
    records.add_metric(record, "broken", "ms", value=float("nan"))
    with pytest.raises(ValueError, match="JSON"):
        records.write_record(record, tmp_path)


def test_unknown_schema_version_is_refused(tmp_path: Path) -> None:
    """load_records() refuses a record from a future format instead of misreading it."""
    path = tmp_path / "future.json"
    path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported schema_version 99"):
        records.load_records(path)


@pytest.mark.parametrize(
    ("cwd", "expected"),
    [
        ("/mnt/c/Users/x/Kiln", "source=9p"),
        ("/home/learner/kiln-bench/labs", "source=ext4-snapshot"),
        ("/home/learner/other", "source=ext4"),
    ],
)
def test_source_location_detects_9p_and_snapshot(cwd: str, expected: str) -> None:
    """Records note whether code ran from slow 9P or the ext4 snapshot (ADR-003)."""
    mounts = "/dev/sdc / ext4 rw 0 0\nC:\\134 /mnt/c 9p rw 0 0\n"
    assert records._source_location(Path(cwd), mounts) == expected
