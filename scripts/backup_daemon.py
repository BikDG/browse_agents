#!/usr/bin/env python3
"""Safety-net sweeper for the append-aware transcript store.

Hooks do the real work now: a Stop hook captures after every turn and a
SessionEnd hook captures the complete transcript at exit (see hook_capture.py).
Those fire exactly when Claude has flushed, which a poller can never know.

This daemon exists only for what hooks cannot cover: a session whose process
was killed before SessionEnd, a session started before the hooks were
installed, or a transcript changed by something other than Claude. It folds
any such drift into the store on a slow interval.

Every capture is lossless. Appends cost only the appended bytes; a shrink or
prefix change archives the previous chain in full, compressed. Nothing is ever
deleted, so there is no retention knob to get wrong.

Stop by deleting data/backup-daemon.pid or killing the pid inside it.
"""
import os
import sys
import time
from pathlib import Path
from ask_paths import PLUGIN_DIR, DATA_DIR

sys.path.insert(0, str(Path(__file__).resolve().parent))
import transcript_store as store

PROJECTS_DIR = Path.home() / ".claude" / "projects"
PID_FILE = DATA_DIR / "backup-daemon.pid"
LOG = DATA_DIR / "hook-capture.log"

# Slow on purpose: hooks handle live sessions, so this only catches drift.
INTERVAL = int(os.environ.get("ASK_EXPERT_BACKUP_INTERVAL", "900"))


def log(msg):
    try:
        with LOG.open("a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} daemon {msg}\n")
    except OSError:
        pass


def sweep():
    folded = diverged = 0
    for p in PROJECTS_DIR.glob("*/*.jsonl"):
        try:
            result = store.update(p.stem, p)
        except Exception as e:
            log(f"{p.stem[:8]} ERROR {type(e).__name__}: {e}")
            continue
        if result.startswith("diverged"):
            diverged += 1
            log(f"{p.stem[:8]} {result}  <-- content changed, archived")
        elif result.startswith(("+", "new")):
            folded += 1
    return folded, diverged


def write_pid():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def should_keep_running():
    if not PID_FILE.exists():
        return False
    try:
        return int(PID_FILE.read_text().strip()) == os.getpid()
    except (OSError, ValueError):
        return False


def main():
    if "--once" in sys.argv:
        f, d = sweep()
        print(f"folded {f} transcript(s), {d} divergence(s) archived")
        return
    write_pid()
    log(f"started (interval {INTERVAL}s, store-backed)")
    try:
        while should_keep_running():
            f, d = sweep()
            if f or d:
                log(f"sweep folded {f}, archived {d}")
            time.sleep(INTERVAL)
    finally:
        try:
            if PID_FILE.exists() and int(PID_FILE.read_text().strip()) == os.getpid():
                PID_FILE.unlink()
        except (OSError, ValueError):
            pass
        log("stopped")


if __name__ == "__main__":
    main()
