#!/usr/bin/env python3
"""Materialize a fork of an existing session via `claude -p --fork-session`.

The fork is created without launching an interactive claude; one minimal turn
is added so the new JSONL has substance. Prints the new session id on stdout,
or an error line starting with `ERROR:` on stderr.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
LINEAGE_FILE = Path(__file__).resolve().parent.parent / "data" / "fork_lineage.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from resolve_session_cwd import decode  # noqa: E402


def record_lineage(new_sid, parent_sid):
    """Persist the fork relationship since claude code's `-p --fork-session`
    doesn't write forkedFrom/logicalParentUuid metadata into the new JSONL."""
    LINEAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if LINEAGE_FILE.exists():
        try:
            data = json.loads(LINEAGE_FILE.read_text())
        except json.JSONDecodeError:
            data = {}
    data[new_sid] = parent_sid
    LINEAGE_FILE.write_text(json.dumps(data, indent=2))


def list_jsonls():
    return {p.resolve() for p in PROJECTS_DIR.glob("*/*.jsonl")}


def main():
    if len(sys.argv) < 2:
        print("usage: create_fork.py <parent-session-id>", file=sys.stderr)
        sys.exit(1)
    parent = sys.argv[1]

    # Locate parent JSONL + its project dir
    parent_jsonl = None
    for p in PROJECTS_DIR.glob(f"*/{parent}.jsonl"):
        parent_jsonl = p
        break
    if parent_jsonl is None:
        print(f"ERROR: parent session {parent} not found on disk", file=sys.stderr)
        sys.exit(1)

    project_dir_name = parent_jsonl.parent.name
    cwd = decode(project_dir_name)
    if cwd and os.path.isdir(cwd):
        os.chdir(cwd)

    before = list_jsonls()

    # Minimal prompt — the fork needs at least one turn to write the file, but
    # we keep it boring so it doesn't pollute the conversation.
    prompt = "(initializing fork — no response needed)"
    try:
        result = subprocess.run(
            ["claude", "-p", "--resume", parent, "--fork-session", prompt],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        print("ERROR: claude -p timed out (>180s)", file=sys.stderr)
        sys.exit(1)
    if result.returncode != 0:
        print(f"ERROR: claude -p exit {result.returncode}: {result.stderr[:500]}", file=sys.stderr)
        sys.exit(1)

    after = list_jsonls()
    new_files = [p for p in (after - before) if p.parent.name == project_dir_name]

    # Pick the one whose forkedFrom points back to the parent
    for p in new_files:
        try:
            for line in p.read_text(errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ff = obj.get("forkedFrom")
                if isinstance(ff, dict) and ff.get("sessionId") == parent:
                    record_lineage(p.stem, parent)
                    print(p.stem)
                    return
        except OSError:
            continue

    # Fallback: if exactly one new file appeared, assume it's the fork
    if len(new_files) == 1:
        record_lineage(new_files[0].stem, parent)
        print(new_files[0].stem)
        return

    print(
        f"ERROR: could not identify new fork session id (new files: {[p.name for p in new_files]})",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
