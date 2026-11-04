"""A short version of K3 lab (a): it runs end to end and writes a valid record (K3 step 3.6).

Run with `./scripts/dev.sh test -m lab tests/lab` (lab tests are excluded by default).
"""

import subprocess
import sys
from pathlib import Path

import pytest

from kiln.bench import records

pytestmark = pytest.mark.lab
REPO = Path(__file__).resolve().parents[2]


def test_cow_lab_writes_a_valid_record(tmp_path: Path) -> None:
    """A 20 MiB graph, 2 workers, 1 repeat, one mode: the record validates and shows sharing."""
    results = tmp_path / "results"
    subprocess.run(
        [
            sys.executable,
            str(REPO / "labs" / "k3" / "k3_lab.py"),
            "cow",
            "--mb",
            "20",
            "--workers",
            "2",
            "--repeats",
            "1",
            "--modes",
            "freeze",
            "--results",
            str(results),
            "--work",
            str(tmp_path / "work"),
        ],
        check=True,
        timeout=300,
        cwd=REPO,
    )
    files = list((results / "K3" / "cow").glob("*.json"))
    assert len(files) == 1
    [record] = records.load_records(files[0])
    assert records.validate(record) == []
    metrics = record["metrics"]
    assert isinstance(metrics, dict)
    shared_p0 = metrics["P0_shared_per_worker_bytes"]["samples"]
    assert len(shared_p0) == 2
    assert min(shared_p0) > 20 * 1024 * 1024  # the graph really is shared after fork
