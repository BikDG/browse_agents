#!/usr/bin/env python3
"""Save the CURRENT Claude Code session and print its resume command.

`/exit` prints `claude --resume "<name>"` on the way out. This does the same
thing without exiting, and additionally keeps a copy of the transcript that
Claude Code's own 30-day cleanup cannot touch.

Honest limitation: a slash command cannot make claude flush its in-memory
turns to disk. What gets copied is whatever has already been written. The
output says how stale that is so the number is never a surprise.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PLUGIN_DIR / "data"
SAVES_DIR = DATA_DIR / "saves"
SAVED_FILE = DATA_DIR / "saved.json"


def find_transcript(sid):
    return next(Path.home().glob(f".claude/projects/*/{sid}.jsonl"), None)


def read_names(path):
    """(session_name, ai_title) from the transcript. Last rename wins."""
    session_name = ai_title = None
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 512 * 1024))
            tail = fh.read().decode("utf8", "replace")
            if '"custom-title"' not in tail and size > 512 * 1024:
                fh.seek(0)
                tail = fh.read().decode("utf8", "replace")
        for line in reversed(tail.splitlines()):
            if session_name is None and '"custom-title"' in line:
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if o.get("type") == "custom-title" and o.get("customTitle"):
                    session_name = o["customTitle"]
            if ai_title is None and '"ai-title"' in line:
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if o.get("type") == "ai-title" and o.get("aiTitle"):
                    ai_title = o["aiTitle"]
            if session_name and ai_title:
                break
    except OSError:
        pass
    return session_name, ai_title


def last_record_time(path):
    """Local time of the last timestamped record in the transcript."""
    import datetime
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 512 * 1024))
            tail = fh.read().decode("utf8", "replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        if '"timestamp"' not in line:
            continue
        try:
            ts = json.loads(line).get("timestamp")
        except json.JSONDecodeError:
            continue
        if not ts:
            continue
        try:
            dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.astimezone().strftime("%H:%M:%S")
        except ValueError:
            return ts
    return None


def load_saved():
    if not SAVED_FILE.exists():
        return {}
    try:
        return json.loads(SAVED_FILE.read_text()).get("saved", {})
    except (json.JSONDecodeError, OSError):
        return {}


def save_saved(saved):
    SAVED_FILE.parent.mkdir(parents=True, exist_ok=True)
    SAVED_FILE.write_text(json.dumps({"saved": saved}, indent=2))


def human_age(seconds):
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{int(seconds // 60)}m"
    if seconds < 172800:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def main():
    sid = (sys.argv[1] if len(sys.argv) > 1 else
           os.environ.get("CLAUDE_CODE_SESSION_ID", "")).strip()
    if not sid:
        print("SAVE_FAILED: no session id "
              "(CLAUDE_CODE_SESSION_ID unset and none passed as an argument)")
        return 1

    path = find_transcript(sid)
    if path is None:
        print(f"SAVE_FAILED: no transcript on disk for {sid[:8]} yet. "
              f"Claude writes the file on its first flush; try again after a turn or two.")
        return 1

    st = path.stat()
    session_name, ai_title = read_names(path)

    # Fold into the append-aware store rather than writing another full copy.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import transcript_store as store
        store_result = store.update(sid, path)
    except Exception as e:
        store_result = f"store error: {e}"

    # No full copy: the store already holds every byte, and current.jsonl is
    # itself a complete, directly usable transcript. Writing another 78 MB per
    # /save is exactly what filled 79 GiB. Record a labelled checkpoint instead.
    dest = store.STORE / sid / "current.jsonl"
    try:
        store.mark(sid, label=os.environ.get("CLAUDE_SAVE_LABEL", "/save"))
    except Exception:
        pass

    saved = load_saved()
    saved[sid] = {
        "name": session_name,
        "ai_title": ai_title,
        "saved_at": time.time(),
        "project": path.parent.name,
        "cwd": os.getcwd(),
        "bytes": st.st_size,
        "copy": str(dest),
    }
    save_saved(saved)

    mb = st.st_size / (1024 * 1024)
    print(f"Saved {mb:.1f} MB  ({store_result})")
    print(f"Stored at {dest}")

    # Report WHERE the save cuts off rather than how stale the file is. "38m
    # ago" reads like lost work even when the session was simply idle; the last
    # recorded turn tells you directly whether anything is missing.
    ends = last_record_time(path)
    if ends:
        print(f"The saved copy ends at {ends}. Anything after that is not in it.")
    print("Your Stop/SessionEnd hooks capture the rest automatically; "
          "/compact or /exit forces it now.")
    print()
    print("Resume this session with:")
    # Native `claude --resume` resolves a SESSION ID only — it does not match
    # the custom-title/name (the picker searches summaries, not custom-title).
    # Always print the UUID command, which works in any shell. Surface the
    # friendly name only as a hint for /browse-experts, which DOES resume by name.
    print(f"  claude --resume {sid}")
    if session_name:
        print()
        print(f'Or in /browse-experts, search "{session_name}" and press enter.')
        print(f'(Native --resume takes the UUID, not the name "{session_name}".)')
    else:
        print()
        print("(No name yet. /rename <name> sets one that shows in /browse-experts;")
        print(" native --resume still needs the UUID above.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
