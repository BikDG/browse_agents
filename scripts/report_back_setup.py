#!/usr/bin/env python3
"""Set up /report-back: identify the current session, locate its parent
(or fall back to self if it's a root), and prepare the report-file path.

Outputs `key=value` lines on stdout that the /report-back slash command
parses to know where to write its summary.
"""
import datetime
import json
import os
import sys
from pathlib import Path
from ask_paths import PLUGIN_DIR, DATA_DIR

REPORTS_DIR = DATA_DIR / "reports"


def find_session_jsonl(sid):
    for p in Path.home().glob(f".claude/projects/*/{sid}.jsonl"):
        return p
    return None


def find_forked_from(jsonl_path):
    try:
        for line in jsonl_path.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            ff = obj.get("forkedFrom")
            if isinstance(ff, dict):
                return ff.get("sessionId")
    except OSError:
        pass
    return None


def main():
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        print("ERROR: CLAUDE_CODE_SESSION_ID is not set in env", file=sys.stderr)
        sys.exit(1)

    jsonl = find_session_jsonl(sid)
    if jsonl is None:
        print(f"ERROR: no JSONL found for session {sid}", file=sys.stderr)
        sys.exit(1)

    parent = find_forked_from(jsonl)
    target = parent or sid
    is_fork = parent is not None

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{target}.md"

    print(f"current_session_id={sid}")
    print(f"current_session_short={sid[:8]}")
    print(f"parent_session_id={parent or '(none)'}")
    print(f"target_session_id={target}")
    print(f"is_fork={'true' if is_fork else 'false'}")
    print(f"report_file_path={report_path}")
    print(f"report_exists={'true' if report_path.exists() else 'false'}")
    print(f"timestamp={datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")


if __name__ == "__main__":
    main()
