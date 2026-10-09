#!/usr/bin/env python3
"""Transcript safety for /browse-experts launches.

Subcommands:
  snapshot <session_id>   Copy the session's JSONL to data/transcript-backups/
                          before it's resumed, so a rollback/clobber is always
                          recoverable. Keeps every snapshot by default; set
                          ASK_EXPERT_KEEP_PER_SESSION to cap the history.
  check-open <session_id> Exit 0 (and print PIDs) if ANOTHER running claude
                          process already has this session open — i.e. resuming
                          it now risks the two processes clobbering each other's
                          transcript tail. Exit 1 if no conflict.
  restore <backup_path>   Copy a backup file back over the live JSONL.

The clobber this guards against: Claude Code's transcript is a single append
file with no locking/versioning. Two processes on one session id, or a
rewound resume, can flush an older view and silently drop the newer tail.
"""
import os
import shutil
import sys
import time
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BACKUP_DIR = DATA_DIR / "transcript-backups"
LOCK_DIR = DATA_DIR / "locks"

# Retention: 0 means KEEP EVERY BACKUP FOREVER, never prune. This used to be 10
# while backup_daemon.py used 20, so taking a manual snapshot silently trimmed
# the daemon's deeper history down to 10. Both now default to unlimited.
KEEP_PER_SESSION = int(os.environ.get("ASK_EXPERT_KEEP_PER_SESSION", "0"))


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True  # exists, owned by someone else


def lock(sid):
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    (LOCK_DIR / sid).write_text(f"{os.getppid()}\n{time.time()}\n")
    return 0


def unlock(sid):
    try:
        (LOCK_DIR / sid).unlink()
    except OSError:
        pass
    return 0


def live_lock(sid):
    """Return the locking pid if a live lock exists for sid, else None.
    Stale locks (pid gone) are cleaned up."""
    f = LOCK_DIR / sid
    if not f.exists():
        return None
    try:
        pid = int(f.read_text().splitlines()[0])
    except (OSError, ValueError, IndexError):
        return None
    if pid_alive(pid):
        return pid
    try:
        f.unlink()  # stale
    except OSError:
        pass
    return None


def find_jsonl(sid):
    for p in PROJECTS_DIR.glob(f"*/{sid}.jsonl"):
        return p
    return None


def snapshot(sid):
    src = find_jsonl(sid)
    if src is None:
        print(f"snapshot: no JSONL for {sid}", file=sys.stderr)
        return 1
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%S")
    dest = BACKUP_DIR / f"{sid}.{ts}.jsonl"
    try:
        shutil.copy2(src, dest)
    except OSError as e:
        print(f"snapshot: copy failed: {e}", file=sys.stderr)
        return 1
    # Prune older backups for this session, keep most recent KEEP.
    if KEEP_PER_SESSION > 0:
        backups = sorted(BACKUP_DIR.glob(f"{sid}.*.jsonl"))
        for old in backups[:-KEEP_PER_SESSION]:
            try:
                old.unlink()
            except OSError:
                pass
    print(f"snapshot: {dest} ({dest.stat().st_size} bytes)", file=sys.stderr)
    return 0


def check_open(sid):
    """Conflict if another plugin-launched resume holds a live lock, or a
    claude process still has sid in its (brief-lived) cmdline."""
    # 1) Live lock written by another plugin-launched `continue`.
    locked_by = live_lock(sid)
    if locked_by:
        print(f"  lock held by pid {locked_by} (another plugin-launched window)", file=sys.stderr)
        return 0
    me = os.getpid()
    parent = os.getppid()
    hits = []
    proc = Path("/proc")
    if not proc.is_dir():
        return 1  # can't check; assume no conflict
    for pdir in proc.iterdir():
        if not pdir.name.isdigit():
            continue
        pid = int(pdir.name)
        if pid in (me, parent):
            continue
        try:
            cmdline = (pdir / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if not cmdline:
            continue
        # A claude process actively on this session id.
        if sid in cmdline and ("claude" in cmdline.lower()):
            # Ignore our own guard/index/fork helpers referencing the id.
            if "session_guard.py" in cmdline or "index_sessions.py" in cmdline \
               or "create_fork.py" in cmdline or "browse_tui.py" in cmdline:
                continue
            hits.append((pid, cmdline.strip()[:100]))
    if hits:
        for pid, cl in hits:
            print(f"  pid {pid}: {cl}", file=sys.stderr)
        return 0  # conflict found
    return 1      # no conflict


def restore(backup_path):
    bp = Path(backup_path)
    if not bp.is_file():
        print(f"restore: no such backup {backup_path}", file=sys.stderr)
        return 1
    # backup filename: <sid>.<ts>.jsonl
    sid = bp.name.split(".")[0]
    dest = find_jsonl(sid)
    if dest is None:
        # session file gone — restore into the most likely project dir
        dest = PROJECTS_DIR / "-home-bik" / f"{sid}.jsonl"
    # Safety-snapshot the current live file before overwriting it.
    if dest.exists():
        snapshot(sid)
    try:
        shutil.copy2(bp, dest)
    except OSError as e:
        print(f"restore: copy failed: {e}", file=sys.stderr)
        return 1
    print(f"restored {bp.name} -> {dest}")
    return 0


def main():
    if len(sys.argv) < 3:
        print("usage: session_guard.py {snapshot|check-open|restore} <arg>", file=sys.stderr)
        return 2
    cmd, arg = sys.argv[1], sys.argv[2]
    if cmd == "snapshot":
        return snapshot(arg)
    if cmd == "check-open":
        return check_open(arg)
    if cmd == "restore":
        return restore(arg)
    if cmd == "lock":
        return lock(arg)
    if cmd == "unlock":
        return unlock(arg)
    print(f"unknown subcommand {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
