#!/usr/bin/env python3
"""Print indexed sessions as a numbered markdown table — used by the
no-display SSH fallback so /browse-experts can still surface the list."""
import json
import os
import sys
from pathlib import Path

INDEX_FILE = Path.home() / ".claude" / "plugins" / "ask-expert" / "data" / "index.json"
STARS_FILE = Path.home() / ".claude" / "plugins" / "ask-expert" / "data" / "stars.json"
TITLES_FILE = Path.home() / ".claude" / "plugins" / "ask-expert" / "data" / "titles.json"


def _load_json(p, default):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def main():
    idx = _load_json(INDEX_FILE, {"sessions": {}})
    sessions = idx.get("sessions", {})
    if not sessions:
        print("(no sessions indexed yet)")
        return
    stars = set(_load_json(STARS_FILE, {"stars": []}).get("stars", []))
    titles = _load_json(TITLES_FILE, {"titles": {}}).get("titles", {})

    rows = sorted(sessions.items(), key=lambda kv: -(kv[1].get("mtime") or 0))
    print("| # | ✪ | Session | Title | Tokens | Topics |")
    print("|---|---|---------|-------|--------|--------|")
    for i, (sid, m) in enumerate(rows[:30], start=1):
        star = "✪" if sid in stars else ""
        title = titles.get(sid) or m.get("title") or "(untitled)"
        title = title.replace("|", "\\|")[:50]
        ct = (m.get("stats") or {}).get("current_tokens") or 0
        if ct >= 1000:
            ct_str = f"{ct // 1000}k"
        else:
            ct_str = str(ct)
        topics = ", ".join((m.get("topics") or [])[:4])[:50]
        print(f"| {i} | {star} | `{sid[:8]}` | {title} | {ct_str} | {topics} |")


if __name__ == "__main__":
    main()
