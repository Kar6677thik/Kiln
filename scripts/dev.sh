#!/usr/bin/env bash
# =============================================================================================
# scripts/dev.sh: ONE entry point for every common Kiln command.
#
# WHY THIS FILE EXISTS
#   Running tools by hand invites small differences: the wrong Python version, a virtual
#   environment on the slow /mnt/c disk, a forgotten check. This script runs every command the
#   same way, every time (docs/spec/08-repo-layout-and-tooling.md §3).
#
# HOW TO RUN IT
#   Inside WSL (Debian), from the repo folder:      ./scripts/dev.sh check
#   From Windows PowerShell or Git Bash:
#     wsl.exe -d Debian --cd "/mnt/c/Users/Karthik Reddy/Documents/Study/Projects/Kiln" -- ./scripts/dev.sh check
#   Choose a Python version with KILN_PY (default 3.15):   KILN_PY=3.13 ./scripts/dev.sh test
#   List the subcommands:                                  ./scripts/dev.sh help
#
# READ FIRST
#   docs/concepts/terminal-and-wsl.md (shells, quoting, exit codes, 9P vs ext4)
#
# BASH CRASH COURSE (for reading this file)
#   VAR="value"          sets a variable (no spaces around "=")
#   "$VAR"               uses it; the double quotes keep spaces in paths safe
#   $(command)           runs a command and substitutes its output
#   cmd1 && cmd2         runs cmd2 only if cmd1 succeeded (exit code 0)
#   case "$x" in a) ...;; esac    picks a branch, like a chain of if/elif
#   "$@"                 all the arguments given to this script (or function), each kept intact
# =============================================================================================

# WHY: stop on the first error (-e), treat unset variables as errors (-u), and make a failure
# anywhere in a pipeline count as a failure (-o pipefail). Without these, bash keeps going
# after errors and you get confusing results later.
set -euo pipefail

# WHY: refuse to run outside Linux. Kiln needs fork(), /proc and signals (ADR-003).
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "dev.sh: run me inside WSL:" >&2
  echo "  wsl.exe -d Debian --cd \"/mnt/c/Users/Karthik Reddy/Documents/Study/Projects/Kiln\" -- ./scripts/dev.sh $*" >&2
  exit 2
fi

# The repo root is the folder above this script. BASH_SOURCE[0] is this script's own path.
# WHY: computed, never hard-coded, and always quoted, because the path contains a space
# ("Karthik Reddy"). See docs/spec/07-environment.md §10.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Which Python to use: the value of KILN_PY if set, otherwise 3.15 (ADR-004).
# ${VAR:-default} means "VAR's value, or default if VAR is empty or unset".
KILN_PY="${KILN_PY:-3.15}"

# WHY: virtual environments live on WSL's fast ext4 disk (~/.cache), never on /mnt/c (9P),
# where Python imports are many times slower (ADR-003). uv reads these two variables:
#   UV_PROJECT_ENVIRONMENT = where the project's venv lives
#   UV_PYTHON              = which interpreter uv must use (so `uv run` never "helpfully"
#                            rebuilds a 3.13 venv with the 3.15 from .python-version)
export UV_PROJECT_ENVIRONMENT="$HOME/.cache/kiln/venvs/py$KILN_PY"
export UV_PYTHON="$KILN_PY"

# WHY: uv installs itself into ~/.local/bin, which isn't on PATH in every kind of shell
# (for example `wsl.exe -- cmd` starts a non-login shell). Fall back to that location.
if command -v uv >/dev/null 2>&1; then
  UV="$(command -v uv)"
else
  UV="$HOME/.local/bin/uv"
fi

# The interpreter tags in the test matrix (ADR-004).
MATRIX=("3.13" "3.14" "3.15" "3.15t")

# Print the list of subcommands. "<<'EOF'" starts a block of literal text that ends at EOF.
usage() {
  cat <<'EOF'
Usage: ./scripts/dev.sh <command> [args]      (choose Python with KILN_PY=3.13|3.14|3.15|3.15t)

  doctor [--milestone Kn]   check this machine (kiln doctor)
  sync [--group G]          create/update the virtual environment (uv sync)
  fmt                       format code (ruff format) and sort imports
  lint                      ruff check + ruff format --check (+ shellcheck if installed)
  typecheck                 mypy --strict on src/kiln (+ gated modules on 3.15)
  test [pytest args]        run the tests (excludes lab and slow tests)
  docs-check                run scripts/check_docs.py (the documentation rules)
  check                     lint + typecheck + test + docs-check: must pass before every commit
  test-matrix               run the tests on 3.13, 3.14, 3.15 and 3.15t
  coverage                  measure test coverage
  bench-snapshot            copy the repo to ~/kiln-bench (ext4) for timing experiments
  lab <id> [args]           run a milestone lab (available from K1)
  run <file.py> [args]      run a Python file with the chosen interpreter and venv
  serve-demo [args]         start a demo Kiln server (available from K1)
  clean                     delete tool caches (never results/ or venvs)
EOF
}

# Run a command inside the project's virtual environment with the chosen Python.
# "$@" passes every argument through unchanged, spaces and all.
uvrun() {
  "$UV" run --quiet "$@"
}

# GOTCHA: `set -e` is silently switched OFF inside a function that is called as part of an
# `a && b` chain (as `check` does below). So every step here ends with `|| return 1`, or a
# failing ruff would be ignored and `check` would carry on. (Found in K1: lint failed, check
# still said "all green".)
cmd_lint() {
  echo "== ruff check (lint) =="
  uvrun ruff check . || return 1
  echo "== ruff format --check (formatting) =="
  uvrun ruff format --check . || return 1
  # WHY: shellcheck finds classic shell-script bugs (unquoted variables...). It is optional
  # (sudo apt install shellcheck), so only run it when it's installed.
  if command -v shellcheck >/dev/null 2>&1; then
    echo "== shellcheck =="
    shellcheck scripts/*.sh || return 1
  fi
}

cmd_typecheck() {
  echo "== mypy (core, checked at the Python 3.13 floor) =="
  uvrun mypy || return 1
  # WHY: modules that use 3.14+/3.15 syntax are checked separately (spec/08 §2.4). The list is
  # empty until K4/K9 create them, so only run the second pass when one exists.
  local gated=()
  for path in src/kiln/bench/report src/kiln/worker/executors/interpreters.py; do
    if [[ -e "$path" ]]; then gated+=("$path"); fi
  done
  if (( ${#gated[@]} > 0 )); then
    echo "== mypy (gated modules, Python 3.15) =="
    uvrun mypy --python-version 3.15 "${gated[@]}" || return 1
  fi
}

cmd_test() {
  echo "== pytest (Python $KILN_PY) =="
  uvrun pytest "$@"
}

cmd_docs_check() {
  echo "== check_docs.py =="
  uvrun python scripts/check_docs.py "$@"
}

cmd_test_matrix() {
  # For each interpreter: build its own venv, run the tests, remember the result.
  local summary=() failed=0 tag log status line
  for tag in "${MATRIX[@]}"; do
    echo "================ Python $tag ================"
    log="$(mktemp)"
    # WHY: each tag gets its own venv folder (py3.13, py3.15t...), selected by these variables.
    # "|| status=$?" stops `set -e` from aborting the loop when one version fails.
    status=0
    UV_PROJECT_ENVIRONMENT="$HOME/.cache/kiln/venvs/py$tag" UV_PYTHON="$tag" \
      "$UV" run --quiet pytest -q 2>&1 | tee "$log" || status=$?
    # The last line pytest prints is its summary, e.g. "42 passed, 3 skipped in 1.2s".
    line="$(grep -E '(passed|failed|error|skipped)' "$log" | tail -n 1 || true)"
    rm -f "$log"
    if (( status == 0 )); then
      summary+=("$(printf '%-6s ✅ %s' "$tag" "$line")")
    else
      summary+=("$(printf '%-6s ❌ %s' "$tag" "$line")")
      failed=1
    fi
  done
  echo
  echo "================ test-matrix summary ================"
  printf '%s\n' "${summary[@]}"
  return "$failed"
}

cmd_coverage() {
  uvrun coverage erase
  uvrun coverage run -m pytest -m "not lab" "$@"
  uvrun coverage combine || true   # WHY: nothing to combine is fine (no subprocesses yet)
  uvrun coverage report
}

cmd_clean() {
  # WHY: only caches that tools rebuild on their own. Never results/ (your measurements)
  # and never the venvs.
  rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis htmlcov
  rm -f .coverage .coverage.*
  find . -name "__pycache__" -type d -prune -exec rm -rf {} +
  echo "cleaned tool caches"
}

# "${1:-help}" is the first argument, or "help" if none was given. "shift" then drops it, so
# "$@" holds only the remaining arguments for the subcommand.
command="${1:-help}"
shift || true

case "$command" in
  help|-h|--help) usage ;;
  doctor)         uvrun kiln doctor "$@" ;;
  sync)           "$UV" sync "$@" ;;
  fmt)            uvrun ruff format . && uvrun ruff check --fix --select I . ;;
  lint)           cmd_lint ;;
  typecheck)      cmd_typecheck ;;
  test)           cmd_test "$@" ;;
  docs-check)     cmd_docs_check "$@" ;;
  # WHY: "&&" chains stop at the first failure, which is what "check" promises.
  check)          cmd_lint && cmd_typecheck && cmd_test && cmd_docs_check && echo "== check: all green ==" ;;
  test-matrix)    cmd_test_matrix ;;
  coverage)       cmd_coverage "$@" ;;
  bench-snapshot) "$REPO_ROOT/scripts/bench-snapshot.sh" ;;
  run)            uvrun python "$@" ;;
  # A lab id is "k<N>-<experiment>", e.g. k1-oom: run labs/k1/k1_lab.py oom.
  # ${1%%-*} keeps the text before the first "-"; ${1#*-} keeps the text after it.
  lab)            [[ $# -ge 1 ]] || { echo "usage: dev.sh lab k1-oom [args]" >&2; exit 2; }
                  milestone="${1%%-*}"; experiment="${1#*-}"; shift
                  uvrun python "labs/$milestone/${milestone}_lab.py" "$experiment" "$@" ;;
  serve-demo)     uvrun kiln serve examples.demo:app --labs "$@" ;;
  clean)          cmd_clean ;;
  *)              echo "dev.sh: unknown command '$command'" >&2; usage >&2; exit 2 ;;
esac
