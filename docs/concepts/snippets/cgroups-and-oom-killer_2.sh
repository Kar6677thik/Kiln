#!/usr/bin/env bash
# requires: linux, a systemd user session (docs/spec/07-environment.md §6.1)
# Run a Python process that wants 200 MiB inside a scope limited to 64 MiB (no swap),
# then show its exit status and the oom_kill counter before and after.
set -u
PY="${PY:-$HOME/.local/bin/python3.15}"
UID_NUM="$(id -u)"
# memory.events is hierarchical: it counts events in this group AND everything below it.
# The scope itself disappears when its process dies, so we watch its parent, app.slice.
APP_SLICE="/sys/fs/cgroup/user.slice/user-$UID_NUM.slice/user@$UID_NUM.service/app.slice"
oom_kills() { awk '$1 == "oom_kill" {print $2}' "$APP_SLICE/memory.events"; }

before="$(oom_kills)"
OUT="$(mktemp)"
# The subshell keeps bash's own "Killed" job message out of the output.
( systemd-run --user --scope --quiet -p MemoryMax=64M -p MemorySwapMax=0 \
    "$PY" -c '
import pathlib
print("child cgroup:", pathlib.Path("/proc/self/cgroup").read_text().split("::")[1].strip(), flush=True)
data = bytearray(200 * 1024 * 1024)   # ask for 200 MiB; the kernel must find real pages
print("allocated?! this line should never print", flush=True)
' >"$OUT" 2>&1 ) 2>/dev/null
status=$?
cat "$OUT"; rm -f "$OUT"
after="$(oom_kills)"
echo "exit status: $status   (128 + 9 = 137: killed by signal 9, SIGKILL)"
echo "app.slice oom_kill: $before -> $after"
