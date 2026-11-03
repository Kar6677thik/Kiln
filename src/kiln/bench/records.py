"""ExperimentRecord: the one JSON format every Kiln lab uses to save a measurement.

WHY THIS MODULE EXISTS
    Kiln's labs measure things (memory per worker, latency, throughput) on several Python
    versions. A number is only useful if you also know *how* it was produced: which
    interpreter, which machine, which code version, which settings. One shared record format
    (ADR-022) means every lab saves that context automatically, the lab reports can cite exact
    files, and K9 can compare everything without per-lab code. The format is defined field by
    field in docs/spec/10-measurement-methodology.md §2.

HOW IT FITS
    a lab script ──new_record()──▶ dict ──add_metric()──▶ dict ──write_record()──▶
        results/<milestone>/<experiment>/<run_id>.json ──load_records()──▶ K9 matrix report

READ FIRST
    docs/spec/10-measurement-methodology.md §2,
    docs/python/variables-and-types.md (dicts),
    docs/python/exceptions.md, docs/python/pathlib.md

MILESTONE
    Introduced in K0. Used by every lab from K1 onward.
"""

import datetime
import json
import os
import platform
import secrets
import subprocess
import sys
import sysconfig
from pathlib import Path

SCHEMA_VERSION = 1
# WHY: spec/10 §2.1 fixes the allowed units. "bytes" instead of "MB" removes any doubt about
# 1000 vs 1024; reports convert for display.
UNITS = frozenset({"bytes", "s", "ms", "us", "ns", "jobs/s", "req/s", "count", "ratio", "percent"})
REQUIRED_FIELDS = (
    "schema_version",
    "milestone",
    "experiment",
    "variant",
    "run_id",
    "repeat_index",
    "started_at",
    "interpreter",
    "host",
    "git",
    "params",
    "metrics",
)
# WHY: this file is src/kiln/bench/records.py, so the repository root is three folders up.
REPO_ROOT = Path(__file__).resolve().parents[3]

# PY: a type alias gives a long type a short, readable name. See docs/python/type-hints.md.
Record = dict[str, object]


def interpreter_tag() -> str:
    """Return the short interpreter name used in file names, such as "3.15" or "3.15t".

    Why:
        Run ids and folder names must say which Python produced them; "t" marks the
        free-threaded build (ADR-004).

    Returns:
        "<major>.<minor>" plus "t" on free-threaded builds.
    """
    tag = f"{sys.version_info.major}.{sys.version_info.minor}"
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        tag += "t"
    return tag


def _gil_enabled() -> bool:
    """Report whether the GIL is on right now.

    Why:
        On a free-threaded build an imported C extension can switch the GIL back on, which
        would make a "free-threaded" measurement misleading (spec/09 §8).

    Returns:
        sys._is_gil_enabled() where it exists (3.13+), otherwise True.
    """
    probe = getattr(sys, "_is_gil_enabled", None)
    if probe is None:
        return True
    return bool(probe())


def _jit_enabled() -> bool | None:
    """Report whether CPython's experimental JIT compiler is active, if that can be known.

    Why:
        The JIT changes performance; spec/10 records it, or null when unknown.

    Returns:
        True/False on versions exposing sys._jit (3.14+), else None.
    """
    jit = getattr(sys, "_jit", None)
    if jit is None:
        return None
    return bool(jit.is_enabled())


def capture_interpreter() -> dict[str, object]:
    """Describe the running Python interpreter (spec/10 §2, "interpreter").

    Why:
        The same code can behave very differently on 3.14.4 vs 3.15t; every number must
        carry the interpreter that produced it.

    Returns:
        A dict with version, implementation, build, gil_enabled_at_end, executable, jit_enabled.
    """
    build = "freethreaded" if sysconfig.get_config_var("Py_GIL_DISABLED") else "gil"
    return {
        "version": platform.python_version(),
        "implementation": sys.implementation.name,
        "build": build,
        "gil_enabled_at_end": _gil_enabled(),
        "executable": sys.executable,
        "jit_enabled": _jit_enabled(),
    }


def _read_text(path: Path) -> str:
    """Read a text file, returning "" if it can't be read.

    Why:
        Host details are best-effort: a missing /proc file must not stop a measurement.

    Args:
        path: the file to read.

    Returns:
        The file's contents or "".
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _source_location(cwd: Path, mounts_text: str) -> str:
    """Say whether the current folder is on WSL's slow 9P mount or a Linux disk.

    Why:
        ADR-003: timing benchmarks must run from the ext4 snapshot. Writing the location into
        every record makes an accidental 9P run visible in the data.

    Args:
        cwd: the current working directory.
        mounts_text: the contents of /proc/mounts (one mount per line).

    Returns:
        "source=9p", "source=ext4-snapshot" (inside ~/kiln-bench) or "source=<fstype>".

    Example:
        >>> mounts = "/dev/sdc / ext4 rw 0 0" + chr(10) + "drvfs /mnt/c 9p rw 0 0"
        >>> _source_location(Path("/mnt/c/x"), mounts)
        'source=9p'
    """
    best_mount = ""
    best_type = "unknown"
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mount_point, fs_type = parts[1], parts[2]
        # WHY: the mount that "owns" a path is the longest mount point that is a prefix of it.
        inside = str(cwd) == mount_point or str(cwd).startswith(mount_point.rstrip("/") + "/")
        if inside and len(mount_point) > len(best_mount):
            best_mount, best_type = mount_point, fs_type
    if best_type == "9p":
        return "source=9p"
    if "kiln-bench" in cwd.parts:
        return "source=ext4-snapshot"
    return f"source={best_type}"


def capture_host() -> dict[str, object]:
    """Describe the machine (spec/10 §2, "host").

    Why:
        CPU count, RAM and WSL-ness explain many differences between runs.

    Returns:
        A dict with kernel, wsl, cpus, cpu_model, mem_total_bytes, pinned_cpus, notes.
    """
    cpu_model = ""
    for line in _read_text(Path("/proc/cpuinfo")).splitlines():
        if line.startswith("model name"):
            cpu_model = line.split(":", 1)[1].strip()
            break
    mem_total = 0
    for line in _read_text(Path("/proc/meminfo")).splitlines():
        if line.startswith("MemTotal:"):
            # WHY: /proc/meminfo says "kB" but means KiB (1024 bytes).
            mem_total = int(line.split()[1]) * 1024
            break
    kernel = platform.release()
    return {
        "kernel": kernel,
        "wsl": "microsoft" in kernel.lower(),
        "cpus": os.cpu_count() or 1,
        "cpu_model": cpu_model,
        "mem_total_bytes": mem_total,
        "pinned_cpus": None,
        "notes": _source_location(Path.cwd(), _read_text(Path("/proc/mounts"))),
    }


def capture_git() -> dict[str, object]:
    """Record which commit the code came from, and whether it had uncommitted changes.

    Why:
        A number from "dirty" (modified, uncommitted) code can't be reproduced exactly; the
        record must say so.

    Returns:
        {"commit": short hash or None, "dirty": True/False or None if git is unavailable}.
    """
    try:
        # WHY: fixed argument lists, no shell, so nothing can be injected.
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}
    return {"commit": commit, "dirty": bool(status.strip())}


def _make_run_id(now: datetime.datetime) -> str:
    """Build a unique, sortable run id such as "20261103T191204Z-py3.15-7f3a".

    Why:
        Sorting run ids alphabetically sorts them by time; the random suffix keeps two runs
        started in the same second apart.

    Args:
        now: the current UTC time.

    Returns:
        The run id.
    """
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-py{interpreter_tag()}-{secrets.token_hex(2)}"


def new_record(
    milestone: str, experiment: str, variant: str, params: dict[str, object], repeat_index: int
) -> Record:
    """Start a new ExperimentRecord with all the automatic context filled in.

    Why:
        Labs should only have to supply what they know (what they varied and measured);
        everything else is captured the same way every time.

    Args:
        milestone: such as "K3".
        experiment: a stable kebab-case name such as "cow-gc-modes".
        variant: which variant of the experiment this run is, such as "freeze".
        params: the settings this run used (workers, sizes...).
        repeat_index: 0-based index of this repeat (warm-ups are not recorded).

    Returns:
        A dict ready for add_metric() and write_record().

    Example:
        >>> record = new_record("K0", "demo", "baseline", {"n": 1}, 0)
        >>> record["schema_version"], record["metrics"]
        (1, {})
    """
    now = datetime.datetime.now(datetime.UTC)
    return {
        "schema_version": SCHEMA_VERSION,
        "milestone": milestone,
        "experiment": experiment,
        "variant": variant,
        "run_id": _make_run_id(now),
        "repeat_index": repeat_index,
        # WHY: ISO 8601 with milliseconds and a "Z" (UTC) suffix, readable by humans and tools.
        "started_at": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "interpreter": capture_interpreter(),
        "host": capture_host(),
        "git": capture_git(),
        "config": {},
        "params": params,
        "metrics": {},
        "notes": "",
    }


def _is_number(value: object) -> bool:
    """Return True for int and float values, but not for bool.

    Why:
        In Python, True and False are a kind of int (True == 1), but a measurement of "True
        milliseconds" is a bug we want to catch.

    Args:
        value: anything.

    Returns:
        Whether value is a real number.
    """
    return isinstance(value, int | float) and not isinstance(value, bool)


def add_metric(
    record: Record,
    name: str,
    unit: str,
    *,
    value: float | None = None,
    samples: list[float] | None = None,
) -> None:
    """Add one measured metric to a record.

    Why:
        Enforces the two most common mistakes at the moment they're made, not later: an
        unknown unit, and giving both a single value and a list of samples (spec/10 §2.1).

    Args:
        record: the record from new_record().
        name: snake_case metric name, such as "pss_per_worker_bytes".
        unit: one of UNITS.
        value: a single number (use this OR samples).
        samples: a list of numbers (use this OR value).

    Raises:
        ValueError: on an unknown unit, or unless exactly one of value/samples is given.
    """
    # PY: the "*" in the parameter list means value and samples must be passed by name:
    # add_metric(r, "x", "ms", value=3). See docs/python/functions.md.
    if unit not in UNITS:
        raise ValueError(f"unknown unit {unit!r}; allowed: {sorted(UNITS)}")
    if (value is None) == (samples is None):
        raise ValueError("give exactly one of value= or samples=")
    metric: dict[str, object] = {"unit": unit}
    if value is not None:
        metric["value"] = value
    else:
        metric["samples"] = list(samples or [])
    metrics = record["metrics"]
    if not isinstance(metrics, dict):
        raise ValueError("record['metrics'] must be a dict")
    metrics[name] = metric


def _validate_metric(name: str, metric: object) -> list[str]:
    """Check one entry of record["metrics"].

    Why:
        Split out of validate() to keep each function small enough to read in one go.

    Args:
        name: the metric's name.
        metric: the metric's value from the record.

    Returns:
        A list of problems (empty if fine).
    """
    if not isinstance(metric, dict):
        return [f"metrics.{name} must be an object"]
    problems = []
    unit = metric.get("unit")
    if unit not in UNITS:
        problems.append(f"metrics.{name}.unit {unit!r} is not one of {sorted(UNITS)}")
    has_value = "value" in metric
    has_samples = "samples" in metric
    if has_value == has_samples:
        problems.append(f"metrics.{name} must have exactly one of 'value' or 'samples'")
    if has_value and not _is_number(metric["value"]):
        problems.append(f"metrics.{name}.value must be a number")
    if has_samples:
        samples = metric["samples"]
        if not isinstance(samples, list) or not all(_is_number(s) for s in samples):
            problems.append(f"metrics.{name}.samples must be a list of numbers")
    return problems


def validate(record: Record) -> list[str]:
    """Check a record against the spec/10 §2 rules and list every problem found.

    Why:
        Returning *all* problems at once (rather than stopping at the first) saves round
        trips; hand-written checks keep Kiln free of third-party dependencies (ADR-005).

    Args:
        record: the record to check.

    Returns:
        A list of human-readable problems; an empty list means the record is valid.

    Example:
        >>> validate({"schema_version": 1})[0]
        "missing required field 'milestone'"
    """
    problems = [f"missing required field {key!r}" for key in REQUIRED_FIELDS if key not in record]
    if "schema_version" in record and record["schema_version"] != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION}")
    for key in ("milestone", "experiment", "variant", "run_id", "started_at"):
        if key in record and not isinstance(record[key], str):
            problems.append(f"{key} must be a string")
    if "repeat_index" in record and not (
        isinstance(record["repeat_index"], int) and not isinstance(record["repeat_index"], bool)
    ):
        problems.append("repeat_index must be an integer")
    if "duration_s" in record and not _is_number(record["duration_s"]):
        problems.append("duration_s must be a number")
    for key in ("interpreter", "host", "git", "params"):
        if key in record and not isinstance(record[key], dict):
            problems.append(f"{key} must be an object")
    metrics = record.get("metrics")
    if "metrics" in record:
        if isinstance(metrics, dict):
            for name, metric in metrics.items():
                problems.extend(_validate_metric(str(name), metric))
        else:
            problems.append("metrics must be an object")
    return problems


def record_path(record: Record, results_root: Path) -> Path:
    """Compute where a record belongs: results/<milestone>/<experiment>/<run_id>.json.

    Why:
        One fixed layout (spec/10 §2.2) means lab reports and K9 always know where to look.

    Args:
        record: a valid record.
        results_root: the results folder, usually <repo>/results.

    Returns:
        The file path for this record.
    """
    # PY: "/" between Path objects joins path parts. See docs/python/pathlib.md.
    folder = results_root / str(record["milestone"]) / str(record["experiment"])
    return folder / f"{record['run_id']}.json"


def write_record(record: Record, results_root: Path | None = None) -> Path:
    """Validate a record, then save it atomically as pretty-printed JSON.

    Why:
        "Atomically" means a crash can never leave half a file: we write a temporary file
        first, then rename it over the final name in one step (os.replace).

    Args:
        record: the record to save.
        results_root: the results folder; defaults to <repo>/results.

    Returns:
        The path of the written file.

    Raises:
        ValueError: if the record is invalid (the message lists every problem) or contains NaN.
    """
    problems = validate(record)
    if problems:
        raise ValueError("invalid ExperimentRecord:\n  " + "\n  ".join(problems))
    root = results_root if results_root is not None else REPO_ROOT / "results"
    path = record_path(record, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # WHY: allow_nan=False raises on NaN/Infinity, which are not valid JSON (spec/10 §2.1:
    # a failed measurement is omitted and explained in notes, never stored as NaN).
    text = json.dumps(record, indent=2, sort_keys=True, allow_nan=False) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    # SYSCALL: os.replace → rename(2), which swaps the name in one indivisible step.
    temporary.replace(path)
    return path


def load_records(path: Path) -> list[Record]:
    """Load one record file, or every *.json record below a folder.

    Why:
        K9 and the lab reports read records back; refusing unknown schema versions with a
        clear message beats misreading them silently.

    Args:
        path: a .json file or a folder to search recursively.

    Returns:
        The records, sorted by file path.

    Raises:
        ValueError: if a file's schema_version is not one this code understands.
    """
    files = sorted(path.rglob("*.json")) if path.is_dir() else [path]
    records: list[Record] = []
    for file in files:
        data = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            version = data.get("schema_version") if isinstance(data, dict) else None
            raise ValueError(
                f"{file}: unsupported schema_version {version!r} "
                f"(this Kiln understands {SCHEMA_VERSION})"
            )
        records.append(data)
    return records
