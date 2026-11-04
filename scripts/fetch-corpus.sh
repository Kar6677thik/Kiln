#!/usr/bin/env bash
# =============================================================================================
# scripts/fetch-corpus.sh: download the real, pre-fix upstream code that the K2 fork-checker
# corpus was distilled from, so `kiln forkcheck` can also be tried on it (stretch goal).
#
# WHY THIS FILE EXISTS
#   corpus/real/ holds small reproductions written fresh for Kiln (ADR-017): we never copy
#   upstream code into the repo. To check the checker against the *original* code, this
#   script clones it into corpus/upstream/, which is git-ignored, and pins each project
#   to an exact commit or tag, so the result is the same for everyone.
#
# USAGE
#   ./scripts/fetch-corpus.sh            (needs git and network access; about 100 MB)
#   then: kiln forkcheck corpus/upstream/krb5/src/tests/t_otp.py
#
# READ FIRST
#   corpus/real/*/PROVENANCE.md, docs/milestones/K2-start-methods-forkcheck.md §5.10
# =============================================================================================

# Stop on errors, unset variables and failed pipelines (see scripts/dev.sh for details).
set -euo pipefail

# The repo root, computed from this script's location and always quoted (the path has a space).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPSTREAM="$REPO_ROOT/corpus/upstream"
mkdir -p "$UPSTREAM"

# krb5: PR #1483 was merged as c2697c127be6 ("Force "fork" method in t_otp.py", 2026-03-30).
# Its first parent, "c2697c127be6^", is the last commit WITHOUT the fix: the pre-fix t_otp.py.
KRB5_URL="https://github.com/krb5/krb5.git"
KRB5_FIX="c2697c127be660cfe2a623e153c347ade50f184b"

# oslo.concurrency: the 7.2.0 tag, the version Debian patched for Python 3.14 (7.2.0-4).
OSLO_URL="https://opendev.org/openstack/oslo.concurrency.git"
OSLO_TAG="7.2.0"

# fetch NAME URL: clone once (later runs reuse the clone and only fetch new history).
fetch() {
  local name="$1" url="$2"
  if [[ -d "$UPSTREAM/$name/.git" ]]; then
    git -C "$UPSTREAM/$name" fetch --quiet --tags origin
  else
    # --filter=blob:none downloads file contents only when needed: a much smaller clone.
    git clone --quiet --filter=blob:none "$url" "$UPSTREAM/$name"
  fi
}

echo "== krb5 (pre-fix: parent of $KRB5_FIX)"
fetch krb5 "$KRB5_URL"
# "--detach" leaves the clone on a fixed commit instead of a branch.
git -C "$UPSTREAM/krb5" checkout --quiet --detach "${KRB5_FIX}^"

echo "== oslo.concurrency (tag $OSLO_TAG)"
fetch oslo.concurrency "$OSLO_URL"
git -C "$UPSTREAM/oslo.concurrency" checkout --quiet --detach "$OSLO_TAG"

# FSL pyfeeds, the git-repo test and the pexpect patches are not fetched: pyfeeds' server
# refused connections during the K2 corpus pass, and the other two are small enough that
# their PROVENANCE.md describes them fully (see corpus/real/*/PROVENANCE.md).

echo "upstream sources ready in: $UPSTREAM"
echo "try: kiln forkcheck \"$UPSTREAM/krb5/src/tests/t_otp.py\""
