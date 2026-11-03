"""Turn a list of Python input lines into a real, honest REPL transcript.

WHY THIS MODULE EXISTS
    The documentation standard says every "Try it" REPL session in the docs must show
    REAL output (docs/standards/documentation.md §0 rule 5 and §3). Typing sessions by
    hand invites mistakes and invented output. This script feeds input lines to Python's
    own interactive console machinery (the `code` module) and records exactly what it
    prints, so a doc writer can paste the result into a Python note.

HOW IT FITS
    docs writer ──writes──▶ a .repl.txt input file (one Python line per line)
    this script ──runs──▶ code.InteractiveConsole ──prints──▶ ">>> line" + real output
    The output is pasted into the "Try it" section of docs/python/*.md or docs/concepts/*.md.

READ FIRST
    docs/python/running-python-and-the-repl.md (what the REPL is)

MILESTONE
    Introduced in K0 (documentation tooling).

Usage:
    python scripts/repl_transcript.py INPUT_FILE        # INPUT_FILE: one REPL line per line
    python scripts/repl_transcript.py - < INPUT_FILE    # or read the lines from stdin

Notes on fidelity:
    - Tracebacks show `File "<console>"` where the real 3.13+ REPL shows
      `File "<python-input-N>"`. Everything else is identical to what the REPL prints.
    - A blank input line ends an indented block, exactly like pressing Enter twice.
"""

# PY: "import" loads a module (a file of Python code) so we can use what it defines.
# See docs/python/modules-and-imports.md.
import code
import contextlib
import io
import sys
from pathlib import Path


def make_transcript(lines: list[str]) -> str:
    """Run REPL input lines through a real interactive console and capture everything.

    Why:
        The docs promise real output. Using the same machinery as the real REPL
        (`code.InteractiveConsole`) means the transcript can't drift from what a
        learner will see when they type the same lines.

    Args:
        lines: the lines a user would type at the `>>>` and `...` prompts.

    Returns:
        The transcript text: each input line with its prompt, followed by whatever
        Python printed in response (values, print() output, tracebacks).

    Example:
        >>> print(make_transcript(["1 + 1"]), end="")
        >>> 1 + 1
        2
    """
    # WHY: a fresh namespace per transcript, named like the real REPL's (__main__-like),
    # so examples never see each other's variables.
    console = code.InteractiveConsole(locals={"__name__": "__console__"})
    transcript = io.StringIO()
    # WHY: "more" is True while Python is waiting for the rest of a multi-line statement
    # (for example after "def f():"), which is when the real REPL shows "... ".
    more = False
    for line in lines:
        prompt = "... " if more else ">>> "
        transcript.write(prompt + line + "\n")
        captured = io.StringIO()
        # PY: "with" runs setup now and guaranteed cleanup at the end of the block.
        # Here it temporarily points print() output and error output at our buffer.
        # See docs/python/context-managers.md.
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            more = console.push(line)
        transcript.write(captured.getvalue())
    return transcript.getvalue()


def main(argv: list[str]) -> int:
    """Read input lines from a file (or stdin) and print the transcript.

    Why:
        Keeps the command-line handling separate from the transcript logic, so the
        logic can be tested and reused.

    Args:
        argv: the command-line arguments after the script name.

    Returns:
        The process exit code: 0 on success, 2 on a usage error.
    """
    if len(argv) != 1:
        print("usage: repl_transcript.py INPUT_FILE | -", file=sys.stderr)
        return 2
    if argv[0] == "-":
        text = sys.stdin.read()
    else:
        text = Path(argv[0]).read_text(encoding="utf-8")
    # PY: splitlines() breaks a string into a list of lines without the "\n" characters.
    lines = text.splitlines()
    sys.stdout.write(make_transcript(lines))
    return 0


# PY: this block runs only when the file is executed as a script, not when it is imported.
# See docs/python/running-python-and-the-repl.md.
if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
