#!/usr/bin/env bash
source "$(dirname "$0")/paths.sh"
# Ensure exactly one transcript backup daemon is running. Idempotent: safe to
# call repeatedly (e.g. from spawn_browse.sh on every /browse-experts launch).
set -u

DATA_DIR=$DATA_DIR
PID_FILE="$DATA_DIR/backup-daemon.pid"
DAEMON=$PLUGIN_ROOT/scripts/backup_daemon.py

# Already running? `kill -0` alone is not enough: pids get recycled, and a
# stale pid file pointing at some unrelated process would make us think the
# daemon is up while no backups are being written. Confirm the cmdline too.
if [ -f "$PID_FILE" ]; then
  pid="$(cat "$PID_FILE" 2>/dev/null)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    if tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q backup_daemon.py; then
      exit 0
    fi
    echo "pid file points at pid $pid which is not the daemon — restarting" >&2
  fi
fi

mkdir -p "$DATA_DIR"

# Detach into its OWN session with setsid, not just nohup. A bare `nohup ... &`
# stays in the caller's process group and session, so when the shell that ran
# this script is torn down (every /browse-experts launch runs it from a
# short-lived claude subprocess) the daemon is killed with it. That is why the
# daemon kept "starting" and then not being there minutes later.
setsid python3 "$DAEMON" </dev/null >/dev/null 2>&1 &

# The daemon writes its own pid; wait briefly and report what actually came up
# rather than the pid of the setsid wrapper.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 0.3
  pid="$(cat "$PID_FILE" 2>/dev/null)"
  if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
    echo "started transcript backup daemon (pid $pid)"
    exit 0
  fi
done

echo "WARNING: transcript backup daemon did not come up" >&2
exit 1
