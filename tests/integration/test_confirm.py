"""Integration tests for `kiln forkcheck --confirm` (§5.9): real interpreters, real start methods.

Includes gate G2.5 (does runpy.run_path(..., run_name="__main__") let spawn/forkserver children
re-import the main module?) and AC-2.4 (each required real case has a confirmed must-fire finding).
"""

import json
import time
from pathlib import Path

import pytest

from kiln.forkcheck import main
from kiln.forkcheck.confirm import confirm_entry, run_method

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
TINY = """
import multiprocessing as mp


def child():
    print("child", __name__, mp.get_start_method())


if __name__ == "__main__":
    p = mp.Process(target=child)
    p.start()
    p.join()
    print("parent", __name__, p.exitcode)
"""


@pytest.fixture
def tiny(tmp_path: Path) -> Path:
    """A tiny script whose child prints the name it was imported under."""
    path = tmp_path / "tiny.py"
    path.write_bytes(TINY.lstrip().encode())
    return path


@pytest.mark.parametrize("mode", ["runpy", "copy"])
def test_g2_5_children_find_the_main_module(tiny: Path, mode: str) -> None:
    """G2.5: under spawn/forkserver the child re-imports ENTRY as __mp_main__ and runs fine.

    Checked for both the runpy bootstrap and the temp-copy fallback; under fork the child is
    a copy of the parent, so it still sees __main__.
    """
    confirmation = confirm_entry(tiny, [], [tiny.parent], timeout=20, mode=mode)
    runs = confirmation.runs
    assert runs["fork"].output == "child __main__ fork\nparent __main__ 0\n"
    for method in ("spawn", "forkserver"):
        assert runs[method].outcome == "ok"
        assert runs[method].output == f"child __mp_main__ {method}\nparent __main__ 0\n"
    assert confirmation.differs  # the printed names differ, so this script "differs"


def test_copy_mode_cleans_up_its_temporary_file(tiny: Path) -> None:
    """The fallback's _kiln_confirm_<name>.py is deleted after the run."""
    run_method(tiny, "spawn", [], [tiny.parent], timeout=20, mode="copy")
    assert sorted(p.name for p in tiny.parent.iterdir()) == ["tiny.py"]


def test_exception_outcome_names_the_type(tmp_path: Path) -> None:
    """An uncaught exception becomes `exception:<Type>`."""
    script = tmp_path / "boom.py"
    script.write_bytes(b"raise KeyError('x')\n")
    assert run_method(script, "spawn", [], [tmp_path], timeout=20).outcome == "exception:KeyError"


def test_exit_code_outcome(tmp_path: Path) -> None:
    """A plain non-zero exit becomes `exit:<code>`."""
    script = tmp_path / "quit.py"
    script.write_bytes(b"import sys\nsys.exit(3)\n")
    assert run_method(script, "fork", [], [tmp_path], timeout=20).outcome == "exit:3"


def test_arguments_reach_the_script(tmp_path: Path) -> None:
    """--confirm-args become sys.argv[1:] of the script."""
    script = tmp_path / "args.py"
    script.write_bytes(b"import sys\nprint(sys.argv[1:])\n")
    run = run_method(script, "fork", ["a", "b c"], [tmp_path], timeout=20)
    assert run.output == "['a', 'b c']\n"


def test_hang_is_killed_with_its_whole_process_group(tmp_path: Path) -> None:
    """A hanging script (and the child it started) is killed after the timeout → `timeout`."""
    marker = tmp_path / "child-alive"
    script = tmp_path / "hang.py"
    script.write_bytes(
        (
            "import multiprocessing as mp, pathlib, time\n"
            "def child():\n"
            "    time.sleep(3)\n"
            f"    pathlib.Path({str(marker)!r}).write_text('survived')\n"
            "if __name__ == '__main__':\n"
            "    mp.Process(target=child).start()\n"
            "    time.sleep(60)\n"
        ).encode()
    )
    run = run_method(script, "fork", [], [tmp_path], timeout=1)
    assert run.outcome == "timeout"
    # WHY: if only the parent had been killed, the child would write the marker at ~3 s.
    time.sleep(3.5)
    assert not marker.exists()


@pytest.mark.parametrize("case", ["krb5-t_otp", "oslo-lockutils", "fsl-pyfeeds"])
def test_required_real_cases_are_confirmed(
    case: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-2.4 / SC-5: at least one must-fire finding is confirmed (behaviour differs)."""
    monkeypatch.chdir(ROOT)
    folder = ROOT / "corpus" / "real" / case
    expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
    argv = [str(folder), "--confirm", str(folder / expected["entry"]), "--format", "json"]
    argv += ["--confirm-timeout", "20"]
    main(argv)
    document = json.loads(capsys.readouterr().out)
    must = {(m["path"], m["line"], m["rule"]) for m in expected["must_fire"]}
    confirmed = [
        f
        for f in document["findings"]
        if (Path(f["path"]).name, f["line"], f["rule"]) in must
        and f["confirmation"] is not None
        and f["confirmation"]["differs"]
    ]
    assert confirmed, document
