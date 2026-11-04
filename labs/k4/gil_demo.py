"""K4 lab (c): watch a C extension turn the GIL back on, and gilwatch name the culprit.

WHY THIS MODULE EXISTS
    The free-threaded build's quietest trap: one import of an extension without `Py_mod_gil`
    re-enables the GIL for the whole process. This script imports each test module in a
    FRESH subprocess (the GIL can't be switched off again once on), with gilwatch installed,
    and records the GIL state before and after, gilwatch's report, and CPython's warning.

HOW IT FITS
    ./scripts/dev.sh lab k4-gil   (run it with KILN_PY=3.15t; on a GIL build it says so)
        for module in kiln_gil_labs.without_gil, kiln_gil_labs.with_gil, wrapper_pkg,
                      and with_gil under PYTHON_GIL=0:
            python -c <probe> ──▶ JSON line ──▶ table + one ExperimentRecord
    Needs the kiln-gil-labs package built (sync --group lab-k4, which needs a C compiler).

READ FIRST
    docs/concepts/free-threading.md, docs/concepts/c-extensions.md,
    labs/k4/gil_reenabler/README.md

MILESTONE
    Introduced in K4.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from kiln.bench import records

REPO = Path(__file__).resolve().parents[2]
PROBE = """
import json, sys, warnings
warnings.simplefilter("always")
from kiln.worker import gilwatch
seen = []
watch = gilwatch.GilWatch(on_event=seen.append)
installed = watch.install()
before = gilwatch.gil_enabled()
import importlib
importlib.import_module(sys.argv[1])
after = gilwatch.gil_enabled()
print(json.dumps({
    "installed": installed, "before": before, "after": after,
    "reports": [[e.module, ">".join(e.import_chain), e.origin, e.forced_off] for e in seen],
    "warnings": watch._warnings,
}))
"""
CASES = (
    ("kiln_gil_labs.without_gil", {}),
    ("kiln_gil_labs.with_gil", {}),
    ("wrapper_pkg", {}),
    ("kiln_gil_labs.with_gil", {"PYTHON_GIL": "0"}),
)


def probe(module: str, extra_env: dict[str, str]) -> dict[str, object]:
    """Import `module` in a fresh interpreter with gilwatch installed.

    Why:
        A fresh process per case: the first flip would otherwise hide every later one.

    Args:
        module: what to import.
        extra_env: extra environment (PYTHON_GIL=0 for the forced-off case).

    Returns:
        The probe's JSON report (plus stderr).
    """
    env = {**os.environ, **extra_env}
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "labs" / "k4"), env.get("PYTHONPATH", "")]
    )  # WHY: wrapper_pkg lives in labs/k4
    done = subprocess.run(
        [sys.executable, "-c", PROBE, module],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=60,
    )
    if done.returncode != 0:
        return {"error": done.stderr.strip().splitlines()[-1] if done.stderr else "failed"}
    result: dict[str, object] = json.loads(done.stdout.strip().splitlines()[-1])
    result["stderr"] = done.stderr.strip()
    return result


def main() -> None:
    """Run every case and print the lab report's GIL table.

    Why:
        The table rows are exactly the lab report's "GIL re-enabling" table (K4 §7).
    """
    parser = argparse.ArgumentParser(description="K4 GIL re-enabling demo")
    parser.add_argument("--results", type=Path, default=REPO / "results")
    args = parser.parse_args()
    free_threaded = not getattr(sys, "_is_gil_enabled", lambda: True)()
    build = "free-threaded" if free_threaded else "GIL build"
    print(f"== K4 GIL demo on {sys.version.split()[0]} ({build})")
    try:
        import kiln_gil_labs  # noqa: F401  # WHY: only checks that it's installed
    except ImportError:
        print(
            "  not measured: kiln-gil-labs isn't built (needs `sudo apt install build-essential`,"
        )
        print("  then `KILN_PY=3.15t ./scripts/dev.sh sync --group lab-k4`)")
        return
    rows = []
    for module, extra in CASES:
        report = probe(module, extra)
        label = module + (" (PYTHON_GIL=0)" if extra else "")
        rows.append({"case": label, **report})
        print(
            f"  {label:42} before={report.get('before')} after={report.get('after')} "
            f"reports={report.get('reports')} warning={bool(report.get('warnings'))}"
        )
    record = records.new_record("K4", "gil", "reenable", {"free_threaded": free_threaded}, 0)
    record["notes"] = json.dumps(rows)
    for index, row in enumerate(rows):
        records.add_metric(
            record, f"case{index}_gil_after", "count", value=int(bool(row.get("after")))
        )
        records.add_metric(
            record, f"case{index}_reports", "count", value=len(row.get("reports") or [])
        )  # type: ignore[arg-type]
    print(f"  record: {records.write_record(record, args.results)}")


if __name__ == "__main__":
    main()
