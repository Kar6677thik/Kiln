#!/usr/bin/env bash
# =============================================================================================
# scripts/bench-snapshot.sh: copy the repo onto WSL's fast ext4 disk for timing experiments.
#
# WHY THIS FILE EXISTS
#   The repo lives on /mnt/c, which WSL reaches through the slow 9P protocol. Anything that
#   measures startup, import or spawn time would mostly measure 9P. Benchmarks therefore run
#   from a copy in ~/kiln-bench (ADR-003, docs/spec/07-environment.md §7). Every
#   ExperimentRecord notes "source=9p" or "source=ext4-snapshot", so mistakes are visible.
#
# USAGE
#   ./scripts/dev.sh bench-snapshot          (then: cd ~/kiln-bench && ./scripts/dev.sh lab ...)
#
# READ FIRST
#   docs/concepts/terminal-and-wsl.md (9P vs ext4)
# =============================================================================================

# Stop on errors, unset variables and failed pipelines (see dev.sh for the explanation).
set -euo pipefail

# The repo root, computed from this script's location and always quoted (path has a space).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SNAPSHOT="$HOME/kiln-bench"

# WHY: rsync copies only what changed since the last snapshot, so refreshing is quick.
if ! command -v rsync >/dev/null 2>&1; then
  echo "bench-snapshot: rsync is not installed. Fix: sudo apt install rsync" >&2
  exit 1
fi

# rsync flags:
#   -a        "archive": copy folders recursively and keep permissions and timestamps
#   --delete  remove files from the snapshot that no longer exist in the repo
#   --exclude skip git history, results (they are written back to the real repo) and caches
# WHY: the trailing "/" on the source means "copy the folder's contents", not the folder itself.
rsync -a --delete \
  --exclude ".git" \
  --exclude "results" \
  --exclude ".pytest_cache" --exclude ".mypy_cache" --exclude ".ruff_cache" \
  --exclude "__pycache__" --exclude ".hypothesis" \
  "$REPO_ROOT/" "$SNAPSHOT/"

echo "snapshot ready: $SNAPSHOT"
echo "next: cd ~/kiln-bench && ./scripts/dev.sh <command>   (results go to the real repo's results/)"
