#!/usr/bin/env python3
"""Capture a transcript into the store from a Claude Code hook.

Wired to Stop and SessionEnd. Measured behaviour on 2.1.277:
  Stop        the user's prompt for the turn IS on disk; the assistant's
              reply for that turn is NOT yet
  SessionEnd  everything is on disk, including the final reply

So Stop gives a fresh capture after every turn and SessionEnd guarantees a
complete one. Together they close the window the 120s poller could not see,
because a poller can only ever read what has already been flushed and has no
idea when that happened.

Contract: this runs inside the user's session. It must never raise, never
block for long, never write to stdout beyond the hook protocol, and never
return non-zero. Losing a capture is acceptable; disturbing a session is not.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "data", "hook-capture.log")


def log(msg):
    try:
        with open(LOG, "a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
    except OSError:
        pass


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "?"
    try:
        raw = sys.stdin.read()
        ev = json.loads(raw) if raw.strip() else {}
    except Exception:
        ev = {}

    sid = ev.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    path = ev.get("transcript_path")
    if not path and sid:
        from pathlib import Path
        hit = next(Path.home().glob(f".claude/projects/*/{sid}.jsonl"), None)
        path = str(hit) if hit else None
    if not sid or not path:
        log(f"{phase} skipped: sid={bool(sid)} path={bool(path)}")
        return

    t0 = time.time()
    try:
        import transcript_store as store
        result = store.update(sid, path)
    except Exception as e:                      # never propagate
        log(f"{phase} {sid[:8]} ERROR {type(e).__name__}: {e}")
        return
    dt = (time.time() - t0) * 1000
    if result not in ("unchanged", "too-small"):
        log(f"{phase} {sid[:8]} {result} ({dt:.0f}ms)")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    finally:
        # Hook protocol: empty JSON object, always exit 0.
        sys.stdout.write("{}")
        sys.exit(0)
