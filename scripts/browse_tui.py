#!/usr/bin/env python3
"""Curses tree TUI for /browse-experts.

Renders indexed Claude Code sessions as a tree built from each session's
`forkedFrom` metadata: forks appear collapsed under their parent.

Keys:
  ↑↓ / j k        navigate visible rows
  → / l / space   expand a collapsed parent
  ← / h           collapse, or jump to parent if already collapsed
  /               search (matches title, topics, summary, first prompt)
  s / S           toggle star (favorite) on selected session
  r / R           rename selected session OR folder
  d               make a new (visual) folder — prompts for a name
  m               move selected thread into a folder (pick by number; 0=unfile)
  p / P           toggle PERMISSIVE mode: launch with --dangerously-skip-permissions
                  (cursor turns pink when on)
  delete          session: move JSONL to data/trash/; folder: delete (unfiles members)
                  ✗ unavailable row: drop the stale folder assignment
  esc             clear search / quit
  PgUp / PgDn     page
  g / G           top / bottom
  enter           session: continue; folder: expand/collapse;
                  pending fork: create it now (launches native --fork-session)
  f / F           add a *pending* fork under the selected session and drop into
                  rename mode (name it first). Press enter on that placeholder
                  to actually create+launch the fork natively. (Native fork is
                  required: headless `-p` forks don't persist on resume.)
  v / F2          panel listing /save'd chats, newest first, showing as many
                  as fit in 80% of the window; ↑↓ to move, enter to jump to
                  one in the tree, 1-9 to quick-pick, esc to close
  F5 / ctrl-r     force a rescan: re-run the indexer, reload everything from
                  disk, and keep your place. Use after finishing a session in
                  another window, or when a session you know exists is absent.
  x               detach the selected fork from its parent (or re-attach it):
                  it becomes its own root in the tree so you can file it in a
                  different folder without moving the whole thread. View-only,
                  the transcript is never rewritten.
  q               quit

Folders are a purely visual grouping — sessions are never moved on disk.
Folder rows are shown in color (data/folders.json).

A folder header reads "(n)" when every filed thread is present, or "(n of m)"
when m-n of them no longer have a transcript on disk. Those show as dimmed ✗
rows so a folder can never claim threads it cannot open.
"""
import curses
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from ask_paths import PLUGIN_DIR, DATA_DIR

INDEX_FILE = DATA_DIR / "index.json"
REPORTS_DIR = DATA_DIR / "reports"
STARS_FILE = DATA_DIR / "stars.json"
TITLES_FILE = DATA_DIR / "titles.json"
TRASH_DIR = DATA_DIR / "trash"
LINEAGE_FILE = DATA_DIR / "fork_lineage.json"
FOLDERS_FILE = DATA_DIR / "folders.json"
CREATE_FORK = PLUGIN_DIR / "scripts" / "create_fork.py"
GUARD = PLUGIN_DIR / "scripts" / "session_guard.py"
INDEXER = PLUGIN_DIR / "scripts" / "index_sessions.py"
SAVED_FILE = DATA_DIR / "saved.json"
DETACHED_FILE = DATA_DIR / "detached.json"

# The session that launched this TUI (set by spawn_browse.sh). Continuing it
# would open the same id in a second claude process and risk clobbering the
# transcript on exit, so the TUI refuses to continue it.
CURRENT_SESSION = os.environ.get("ASK_EXPERT_CURRENT_SESSION", "").strip()


def load_folders():
    """Return (folders, assignments).
    folders: list of {"id","name","color"} (color = palette index).
    assignments: {root_session_id: folder_id} — purely visual grouping.
    """
    if not FOLDERS_FILE.exists():
        return [], {}
    try:
        data = json.loads(FOLDERS_FILE.read_text())
        return data.get("folders", []), data.get("assignments", {})
    except (json.JSONDecodeError, OSError):
        return [], {}


def save_folders(folders, assignments):
    FOLDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    FOLDERS_FILE.write_text(
        json.dumps({"folders": folders, "assignments": assignments}, indent=2)
    )


def new_folder_id():
    import uuid
    return "f_" + uuid.uuid4().hex[:8]


PENDING_FILE = DATA_DIR / "pending_forks.json"


def load_pending():
    """Pending (not-yet-created) forks: {placeholder_id: {parent, name}}.
    A placeholder is shown in the tree under its parent; pressing Enter on it
    launches the native fork and the placeholder is replaced by the real id."""
    if not PENDING_FILE.exists():
        return {}
    try:
        return json.loads(PENDING_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_pending(pending):
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text(json.dumps(pending, indent=2))


def new_pending_id():
    import uuid
    return "pending_" + uuid.uuid4().hex[:8]


def load_detached():
    """Session ids the user pulled out of their fork tree with `x`.

    A detached session is shown as its own root even though its transcript
    still records a `forkedFrom` parent. Nothing on disk is rewritten: this is
    a view-level override, so it can be undone and never risks the transcript.
    Its purpose is to let one child be filed in a different folder without
    dragging the whole tree along.
    """
    if not DETACHED_FILE.exists():
        return set()
    try:
        return set(json.loads(DETACHED_FILE.read_text()).get("detached", []))
    except (json.JSONDecodeError, OSError):
        return set()


def save_detached(detached):
    DETACHED_FILE.parent.mkdir(parents=True, exist_ok=True)
    DETACHED_FILE.write_text(json.dumps({"detached": sorted(detached)}, indent=2))


def load_lineage():
    if not LINEAGE_FILE.exists():
        return {}
    try:
        return json.loads(LINEAGE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def load_stars():
    if not STARS_FILE.exists():
        return set()
    try:
        return set(json.loads(STARS_FILE.read_text()).get("stars", []))
    except (json.JSONDecodeError, OSError):
        return set()


def save_stars(stars):
    STARS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STARS_FILE.write_text(json.dumps({"stars": sorted(stars)}, indent=2))


def load_titles():
    if not TITLES_FILE.exists():
        return {}
    try:
        return json.loads(TITLES_FILE.read_text()).get("titles", {})
    except (json.JSONDecodeError, OSError):
        return {}


def save_titles(titles):
    TITLES_FILE.parent.mkdir(parents=True, exist_ok=True)
    TITLES_FILE.write_text(json.dumps({"titles": titles}, indent=2))


def load_saved():
    """Sessions explicitly saved with /save, newest first.

    Written by scripts/save_session.py as {sid: {name, saved_at, bytes, ...}}.
    """
    if not SAVED_FILE.exists():
        return []
    try:
        data = json.loads(SAVED_FILE.read_text()).get("saved", {})
    except (json.JSONDecodeError, OSError):
        return []
    rows = [dict(rec, sid=sid) for sid, rec in data.items()]
    rows.sort(key=lambda r: -(r.get("saved_at") or 0))
    return rows


def rel_age(ts):
    if not ts:
        return "?"
    d = max(0, time.time() - ts)
    if d < 90:
        return f"{int(d)}s ago"
    if d < 5400:
        return f"{int(d // 60)}m ago"
    if d < 172800:
        return f"{int(d // 3600)}h ago"
    return f"{int(d // 86400)}d ago"


PANEL_CHROME = 4          # title, blank, blank, hint
PANEL_SCREEN_FRACTION = 0.8


def saved_panel_capacity(screen_h):
    """How many saved-chat rows fit while the panel stays within 80% of the
    window. Always leaves room for at least one row."""
    return max(1, int(screen_h * PANEL_SCREEN_FRACTION) - PANEL_CHROME)


def saved_panel(stdscr, rows):
    """Overlay listing the most recently /save'd chats, newest first, showing
    as many as fit in 80% of the window. Returns the sid the user picked, or
    None if they dismissed it.

    Navigable, because the list is no longer capped at a single keystroke's
    worth: ↑↓/jk move, enter picks, 1-9 still quick-pick the first nine.
    """
    total = len(rows)
    shown = rows[:saved_panel_capacity(stdscr.getmaxyx()[0])]
    sel = 0
    while True:
        h, w = stdscr.getmaxyx()
        # Recompute on every pass so a resize mid-panel stays within budget.
        cap = saved_panel_capacity(h)
        if len(shown) > cap:
            shown = shown[:cap]
            sel = min(sel, len(shown) - 1)
        body = max(1, len(shown))
        height = body + PANEL_CHROME
        width = min(max(58, w - 8), max(10, w - 2))
        top = max(0, (h - height) // 2)
        left = max(0, (w - width) // 2)
        blank = " " * width
        for i in range(height):
            safe_addstr(stdscr, top + i, left, blank, curses.A_REVERSE)

        if total > len(shown):
            title = f" saved chats — {len(shown)} of {total}, newest first "
        else:
            title = f" saved chats — {total} saved, newest first "
        safe_addstr(stdscr, top, left, title.ljust(width)[:width],
                    curses.A_REVERSE | curses.A_BOLD)

        if not shown:
            safe_addstr(stdscr, top + 2, left + 2,
                        "nothing saved yet — run /save inside a session"[:width - 4],
                        curses.A_REVERSE)
        for i, r in enumerate(shown):
            nm = r.get("name") or r.get("ai_title") or r["sid"][:8]
            mb = (r.get("bytes") or 0) / (1024 * 1024)
            age = rel_age(r.get("saved_at"))
            unnamed = "" if r.get("name") else "  (unnamed)"
            tag = f"{i + 1}" if i < 9 else " "
            marker = "▸" if i == sel else " "
            line = f" {marker}{tag:>2}  {nm}{unnamed}"
            right = f"{age}   {mb:.1f} MB "
            pad = max(1, width - len(line) - len(right))
            attr = curses.A_REVERSE | (curses.A_BOLD if i == sel else 0)
            safe_addstr(stdscr, top + 2 + i, left,
                        (line + " " * pad + right)[:width], attr)

        hint = " ↑↓ move   enter = jump to it   1-9 = quick pick   esc = close "
        safe_addstr(stdscr, top + height - 1, left, hint.ljust(width)[:width],
                    curses.A_REVERSE | curses.A_DIM)
        stdscr.refresh()

        key = stdscr.getch()
        if key in (curses.KEY_DOWN, ord("j")):
            sel = min(sel + 1, len(shown) - 1) if shown else 0
        elif key in (curses.KEY_UP, ord("k")):
            sel = max(sel - 1, 0)
        elif key in (curses.KEY_NPAGE, curses.KEY_END, ord("G")):
            sel = max(0, len(shown) - 1)
        elif key in (curses.KEY_PPAGE, curses.KEY_HOME, ord("g")):
            sel = 0
        elif key in (curses.KEY_ENTER, 10, 13):
            return shown[sel]["sid"] if shown else None
        elif ord("1") <= key <= ord("9"):
            i = key - ord("1")
            if i < len(shown):
                return shown[i]["sid"]
        elif key == curses.KEY_RESIZE:
            continue
        else:                       # esc, q, anything else
            return None


_name_cache = {}


def session_name_for(sid, meta):
    """The session's OWN name, i.e. what `/rename` set inside the transcript.

    This is distinct from the TUI title: it is the string `claude --resume`
    accepts in place of a UUID, and it is what /exit prints on the way out.
    Prefer the indexed value; fall back to reading the transcript so sessions
    indexed before session_name existed still show it without a reclassify.
    """
    got = meta.get("session_name")
    if got:
        return got
    key = (sid, meta.get("mtime"))
    if key in _name_cache:
        return _name_cache[key]
    name = None
    path = meta.get("path")
    if path and os.path.isfile(path):
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as fh:
                # Renames append a fresh record, so the last one wins. Scan the
                # tail first; only pay for a full read if the tail has none.
                fh.seek(max(0, size - 512 * 1024))
                chunks = [fh.read().decode("utf8", "replace")]
                if '"custom-title"' not in chunks[0] and size > 512 * 1024:
                    fh.seek(0)
                    chunks = [fh.read().decode("utf8", "replace")]
            for line in reversed(chunks[0].splitlines()):
                if '"custom-title"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "custom-title" and obj.get("customTitle"):
                    name = obj["customTitle"]
                    break
        except OSError:
            name = None
    _name_cache[key] = name
    return name


def display_title(sid, meta, custom_titles):
    """User override > Claude Code's ai-title > '(untitled)'."""
    if sid in custom_titles and custom_titles[sid]:
        return custom_titles[sid]
    return meta.get("title") or "(untitled)"


def save_index(sessions):
    """Persist the in-memory sessions dict back to index.json."""
    try:
        existing = json.loads(INDEX_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        existing = {"version": 1}
    existing["sessions"] = sessions
    INDEX_FILE.write_text(json.dumps(existing, indent=2))


def trash_session(sid, sessions):
    """Move the session JSONL to data/trash/ and drop it from in-memory state.
    Returns (ok, message)."""
    meta = sessions.get(sid)
    if not meta:
        return False, "no such session in index"
    src = meta.get("path")
    if not src or not os.path.isfile(src):
        # File is gone; just drop the entry
        sessions.pop(sid, None)
        return True, f"entry removed (file already missing)"
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%S")
    dest_name = f"{Path(src).stem}.{ts}.jsonl"
    dest = TRASH_DIR / dest_name
    try:
        shutil.move(src, dest)
    except OSError as e:
        return False, f"move failed: {e}"
    sessions.pop(sid, None)
    return True, f"moved to {dest}"


def add_fork_to_index(new_sid, sessions):
    """After create_fork.py reports a new sid, parse its JSONL and add a
    minimal entry to the in-memory sessions dict. Returns True on success."""
    # Find the new JSONL
    jsonl_path = None
    for p in Path.home().glob(f".claude/projects/*/{new_sid}.jsonl"):
        jsonl_path = p
        break
    if jsonl_path is None:
        return False
    # Defer the import so the TUI doesn't pay for it unless needed
    sys.path.insert(0, str(PLUGIN_DIR / "scripts"))
    from index_sessions import parse_session, build_entry
    parsed = parse_session(jsonl_path)
    if parsed.get("skip"):
        return False
    try:
        mtime = jsonl_path.stat().st_mtime
    except OSError:
        return False
    entry = build_entry(new_sid, jsonl_path, mtime, parsed, None)
    # Apply the persisted fork lineage recorded by create_fork.py — claude -p
    # --fork-session does not write forkedFrom/logicalParentUuid into the new
    # JSONL, so we rely on our sidecar.
    if not entry.get("forked_from"):
        lineage = load_lineage()
        if new_sid in lineage:
            entry["forked_from"] = lineage[new_sid]
    sessions[new_sid] = entry
    save_index(sessions)
    return True


def find_transcript(sid):
    """Path to sid's JSONL under ~/.claude/projects, or None."""
    return next(Path.home().glob(f".claude/projects/*/{sid}.jsonl"), None)


def resolve_launch_dir(sid, project=None, cwd=None):
    """Directory that `claude --resume <sid>` must be launched from, or None.

    claude resolves a session id ONLY within the project dir matching the
    current working directory, so the directory actually holding the JSONL is
    the authority. The index's `project`/`cwd` are hints used when the file's
    own directory name can't be decoded.

    This exists because a pending fork carries no `project`/`cwd` at all, so the
    fork launched from wherever /browse-experts was invoked and claude failed
    with "No conversation found with session ID".
    """
    live = find_transcript(sid)
    if live:
        owner = decode_project_dir(live.parent.name)
        if owner and os.path.isdir(owner):
            return owner
    for candidate in (decode_project_dir(project or ""), cwd):
        if candidate and os.path.isdir(candidate):
            return candidate
    return None


def decode_project_dir(name):
    """Decode an encoded project-dir name like '-home-bik-workspace-TI-am6254-nixos'
    back to a real filesystem path. Each '-' is ambiguous: it can mean either
    a path separator OR a literal '-' in a directory name. Try every 2^(n-1)
    partition of the parts and return the path that actually exists on disk.
    Prefers the partition with the most path components (typical case).
    """
    if not name:
        return None
    parts = name.lstrip("-").split("-")
    n = len(parts)
    if n == 0:
        return None
    best = None
    for mask in range(1 << (n - 1)):
        s = parts[0]
        for i in range(n - 1):
            s += "/" if (mask >> i) & 1 else "-"
            s += parts[i + 1]
        path = "/" + s
        if os.path.isdir(path):
            depth = path.count("/")
            if best is None or depth > best[0]:
                best = (depth, path)
    return best[1] if best else None


def report_path_for(sid):
    return REPORTS_DIR / f"{sid}.md"


def load_report_text(sid):
    """Return the report content for a session, or '' if no reports exist."""
    p = report_path_for(sid)
    if not p.exists():
        return ""
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def load_index():
    if not INDEX_FILE.exists():
        return {}
    try:
        return json.loads(INDEX_FILE.read_text()).get("sessions", {})
    except json.JSONDecodeError:
        return {}


def build_tree(sessions, detached=None):
    """Return (roots, children_map). roots are top-level session ids
    sorted newest-first; children_map[parent_id] is a list of child ids
    also sorted newest-first.

    Cycle-safe: if forked_from links form a loop (A→B→A, which can happen from
    bad lineage edits), the edge that would close the loop is dropped so the
    node surfaces as a root rather than vanishing or causing infinite recursion.
    """
    indexed_ids = set(sessions.keys())
    detached = detached or set()

    def parent_of(sid):
        if sid in detached:      # pulled out of its tree by the user
            return None
        p = sessions.get(sid, {}).get("forked_from")
        if p and p in indexed_ids and p != sid:
            return p
        return None

    children = {}
    roots = []
    for sid in sessions:
        parent = parent_of(sid)
        if parent is None:
            roots.append(sid)
            continue
        # Walk up from parent; if we reach sid, this edge closes a cycle — drop
        # it and treat sid as a root instead.
        cursor = parent
        seen = {sid}
        cyclic = False
        while cursor is not None:
            if cursor in seen:
                cyclic = True
                break
            seen.add(cursor)
            cursor = parent_of(cursor)
        if cyclic:
            roots.append(sid)
        else:
            children.setdefault(parent, []).append(sid)

    def mtime(sid):
        return sessions.get(sid, {}).get("mtime") or 0

    roots.sort(key=lambda s: -mtime(s))
    for k in children:
        children[k].sort(key=lambda s: -mtime(s))
    return roots, children


def flatten(roots, children, expanded):
    """Walk the tree depth-first, emitting (sid, depth, has_children, is_expanded)
    for each currently-visible row."""
    rows = []

    seen = set()

    def visit(sid, depth):
        if sid in seen:  # defensive: never recurse into the same node twice
            return
        seen.add(sid)
        kids = children.get(sid, [])
        is_open = sid in expanded
        rows.append((sid, depth, bool(kids), is_open))
        if is_open:
            for c in kids:
                visit(c, depth + 1)

    for r in roots:
        visit(r, 0)
    return rows


def find_root(sid, sessions, detached=None):
    """Walk forked_from up to the top of sid's tree (cycle-safe).

    Stops at a detached node, which is a root as far as the view is concerned —
    otherwise filing a detached child would silently file its original tree.
    """
    detached = detached or set()
    cur = sid
    seen = set()
    while cur and cur not in seen:
        seen.add(cur)
        if cur in detached:
            break
        parent = sessions.get(cur, {}).get("forked_from")
        if parent and parent in sessions and parent != cur:
            cur = parent
        else:
            break
    return cur


def folder_members(fid, roots, assignments, known):
    """Split a folder's assigned threads into (available, missing).

    `available` are root sids still present in the index and renderable as
    trees. `missing` are sids the folder still claims but whose transcript is
    gone from ~/.claude/projects, so the indexer dropped them. Those used to
    vanish silently while the folder header kept counting them, which is why a
    folder could read "(5)" and expand to nothing.
    """
    available = [r for r in roots if assignments.get(r) == fid]
    missing = [
        sid for sid, f in assignments.items()
        if f == fid and sid not in known
    ]
    return available, missing


def build_rows(roots, children, expanded, folders, assignments, pending=None,
               known=None):
    """Build the displayed row list with folder grouping.

    Each row is a 5-tuple: (kind, ident, depth, has_kids, is_open) where kind
    is "folder", "session", "pending", or "missing". Folders render at depth 0
    with their assigned root-trees nested at depth 1; unfiled roots stay at
    depth 0. Pending forks render as children of their parent session.
    "missing" rows are tombstones for filed threads whose transcript is gone.

    `known` is the set of indexed session ids; assignments outside it become
    tombstones instead of disappearing.
    """
    pending = pending or {}
    if known is None:
        known = set(roots) | set(children)
    folder_ids = {f["id"] for f in folders}

    # parent_sid -> [placeholder_id, ...]
    pend_by_parent = {}
    for pid, rec in pending.items():
        pend_by_parent.setdefault(rec.get("parent"), []).append(pid)

    def visit(sid, depth, seen, out):
        if sid in seen:
            return
        seen.add(sid)
        kids = children.get(sid, [])
        pends = pend_by_parent.get(sid, [])
        is_open = sid in expanded
        out.append(("session", sid, depth, bool(kids or pends), is_open))
        if is_open:
            for c in kids:
                visit(c, depth + 1, seen, out)
            for pid in pends:
                out.append(("pending", pid, depth + 1, False, False))

    rows = []
    seen = set()

    # Folders first, in declared order, each holding its assigned root-trees.
    for f in folders:
        fid = f["id"]
        members, gone = folder_members(fid, roots, assignments, known)
        f_open = fid in expanded
        rows.append(("folder", fid, 0, bool(members or gone), f_open))
        if f_open:
            for r in members:
                visit(r, 1, seen, rows)
            for sid in gone:
                rows.append(("missing", sid, 1, False, False))

    # Then unfiled roots (no assignment, or assigned to a folder that's gone).
    for r in roots:
        a = assignments.get(r)
        if a in folder_ids:
            continue
        visit(r, 0, seen, rows)

    return rows


def matches_query(meta, query, custom_title="", sid=""):
    q = query.lower().strip()
    if not q:
        return True
    haystack = " ".join([
        # The session id itself: you often arrive holding only an id (from a
        # log, a URL, another window) and searching it used to return nothing.
        sid or "",
        # The session's own name, what `/rename` set and what `--resume`
        # accepts. Read the indexed value, or a cache entry the detail pane
        # already warmed; never touch disk here, since this runs for every
        # session on every keystroke.
        meta.get("session_name")
        or _name_cache.get((sid, meta.get("mtime")))
        or "",
        custom_title or "",
        meta.get("title") or "",
        meta.get("summary") or "",
        " ".join(meta.get("topics") or []),
        meta.get("first_prompt") or "",
        meta.get("searchable_text") or "",
        meta.get("project") or "",
    ]).lower()
    return all(term in haystack for term in q.split())


def filtered_flat(sessions, query, custom_titles=None):
    if custom_titles is None:
        custom_titles = {}
    hits = [
        (sid, meta) for sid, meta in sessions.items()
        if matches_query(meta, query, custom_titles.get(sid, ""), sid)
    ]
    hits.sort(key=lambda kv: -(kv[1].get("mtime") or 0))
    return [("session", sid, 0, False, False) for sid, _ in hits]


def short(s, n):
    s = (s or "").replace("\n", " ").replace("\t", " ")
    if n <= 1:
        return ""
    return s if len(s) <= n else s[: n - 1] + "…"


def wrap_text(text, width):
    if width <= 0:
        return []
    out = []
    for line in (text or "").splitlines() or [""]:
        if not line:
            out.append("")
            continue
        out.extend(textwrap.wrap(line, width=width) or [""])
    return out


def search_terms(query):
    """The individual terms `/` is matching on, lowercased."""
    return [t for t in (query or "").lower().split() if t]


def highlight_attr():
    try:
        return curses.color_pair(SEARCH_PAIR) | curses.A_BOLD
    except curses.error:
        return curses.A_REVERSE


def split_hits(text, terms):
    """Cut `text` into (segment, is_hit) runs for any case-insensitive term.

    Overlapping matches are merged so a segment is never drawn twice.
    """
    if not text or not terms:
        return [(text, False)]
    low = text.lower()
    spans = []
    for t in terms:
        start = 0
        while True:
            i = low.find(t, start)
            if i < 0:
                break
            spans.append((i, i + len(t)))
            start = i + 1          # allow overlapping occurrences
    if not spans:
        return [(text, False)]
    spans.sort()
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out = []
    pos = 0
    for a, b in merged:
        if a > pos:
            out.append((text[pos:a], False))
        out.append((text[a:b], True))
        pos = b
    if pos < len(text):
        out.append((text[pos:], False))
    return out


def addstr_hits(stdscr, y, x, text, terms, base_attr=curses.A_NORMAL):
    """safe_addstr, but every search hit inside `text` is highlighted."""
    if not terms:
        safe_addstr(stdscr, y, x, text, base_attr)
        return
    col = x
    for seg, hit in split_hits(text, terms):
        if not seg:
            continue
        safe_addstr(stdscr, y, col, seg, highlight_attr() if hit else base_attr)
        col += len(seg)


def safe_addstr(stdscr, y, x, text, attr=curses.A_NORMAL):
    try:
        stdscr.addstr(y, x, text, attr)
    except curses.error:
        pass


def decode_project(proj):
    return (proj or "").lstrip("-").replace("-", "/")


def render_row(sid, depth, has_kids, is_open, meta, starred=False, custom_titles=None):
    if custom_titles is None:
        custom_titles = {}
    indent = "  " * depth
    star = "✪ " if starred else "  "
    if has_kids:
        tree = "▼ " if is_open else "▶ "
    else:
        tree = "" if depth > 0 else "· "
    title = display_title(sid, meta, custom_titles)
    topics = ", ".join(meta.get("topics", []))
    return f"{indent}{star}{tree}{title}  ·  {topics}"


PINK_PAIR = 1  # curses color-pair index for the permissive-mode cursor

# Folder color pairs occupy indices FOLDER_PAIR_BASE .. +len(FOLDER_BG)-1.
SEARCH_PAIR = 9          # search-hit highlight; below FOLDER_PAIR_BASE
FOLDER_PAIR_BASE = 10
# 256-color background palette — distinct, readable with black text.
FOLDER_BG_256 = [39, 208, 78, 213, 220, 51, 171, 203, 156, 117, 229, 141]
FOLDER_BG_8 = [
    curses.COLOR_BLUE, curses.COLOR_GREEN, curses.COLOR_MAGENTA,
    curses.COLOR_CYAN, curses.COLOR_YELLOW, curses.COLOR_RED,
]
_n_folder_pairs = 0


def init_colors():
    """Set up the pink cursor pair plus the folder color palette. Safe no-op
    if the terminal lacks color support."""
    global _n_folder_pairs
    try:
        curses.start_color()
        curses.use_default_colors()
        if curses.COLORS >= 256:
            curses.init_pair(PINK_PAIR, curses.COLOR_BLACK, 218)
            palette = FOLDER_BG_256
        else:
            curses.init_pair(PINK_PAIR, curses.COLOR_BLACK, curses.COLOR_MAGENTA)
            palette = FOLDER_BG_8
        # Search hits: black on yellow reads in both light and dark terminals.
        curses.init_pair(SEARCH_PAIR, curses.COLOR_BLACK,
                         226 if curses.COLORS >= 256 else curses.COLOR_YELLOW)
        for i, bg in enumerate(palette):
            curses.init_pair(FOLDER_PAIR_BASE + i, curses.COLOR_BLACK, bg)
        _n_folder_pairs = len(palette)
    except curses.error:
        _n_folder_pairs = 0


def n_folder_colors():
    # Fall back to a small count if colors weren't initialized (e.g. tests).
    return _n_folder_pairs or len(FOLDER_BG_256)


def folder_attr(color_idx, selected=False):
    try:
        attr = curses.color_pair(FOLDER_PAIR_BASE + (color_idx % n_folder_colors()))
        return attr | (curses.A_REVERSE | curses.A_BOLD if selected else curses.A_BOLD)
    except curses.error:
        return curses.A_REVERSE if selected else curses.A_BOLD


def cursor_attr(permissive):
    if permissive:
        try:
            return curses.color_pair(PINK_PAIR) | curses.A_BOLD
        except curses.error:
            return curses.A_REVERSE
    return curses.A_REVERSE


def show_busy(stdscr, msg):
    """Paint a single-line banner across the bottom and refresh — for blocking
    operations like fork creation so the user sees what's happening."""
    h, w = stdscr.getmaxyx()
    safe_addstr(stdscr, h - 1, 0, msg.ljust(w)[:w], curses.A_REVERSE | curses.A_BOLD)
    stdscr.refresh()


def confirm_prompt(stdscr, msg):
    """Block-and-poll the user for a y/n answer. Returns True on 'y'."""
    show_busy(stdscr, msg)
    while True:
        try:
            k = stdscr.getch()
        except KeyboardInterrupt:
            return False
        if k in (ord("y"), ord("Y")):
            return True
        if k in (ord("n"), ord("N"), 27, curses.KEY_BACKSPACE):
            return False


def draw(stdscr, sessions, rows, cursor, scroll, search_query=None, search_active=False, stars=None, permissive=False, custom_titles=None, rename_active=False, rename_buffer="", folders=None, assignments=None, status_msg=None, pending=None, detached_ids=None, sem_query=None):
    if stars is None:
        stars = set()
    if custom_titles is None:
        custom_titles = {}
    folders = folders or []
    assignments = assignments or {}
    pending = pending or {}
    detached_ids = detached_ids or set()
    folder_by_id = {f["id"]: f for f in folders}
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    if h < 8 or w < 60:
        safe_addstr(stdscr, 0, 0, "terminal too small — resize and retry")
        stdscr.refresh()
        return

    body_h = h - 2
    list_w = max(40, int(w * 0.45))
    detail_x = list_w + 2
    detail_w = w - detail_x

    if sem_query:
        title_bar = f" /browse-experts — FAISS: {sem_query}  (esc=clear) "
    elif rename_active:
        title_bar = f" rename: {rename_buffer}_  (enter=save, esc=cancel) "
    elif search_active:
        title_bar = f" search: {search_query}_  (enter=confirm, esc=cancel) "
    elif search_query:
        title_bar = f" /browse-experts — filtered by: {search_query}  (esc=clear) "
    else:
        title_bar = " /browse-experts — pick a past session to fork "
    if permissive and not rename_active and not search_active:
        title_bar += " · PERMISSIVE (--dangerously-skip-permissions)"
    safe_addstr(stdscr, 0, 0, title_bar.ljust(w)[:w], curses.A_REVERSE)

    sel_attr = cursor_attr(permissive)
    for i in range(body_h):
        idx = scroll + i
        if idx >= len(rows):
            break
        kind, ident, depth, has_kids, is_open = rows[idx]
        is_cursor = idx == cursor
        if kind == "folder":
            f = folder_by_id.get(ident, {})
            tree = "▼ " if (has_kids and is_open) else ("▶ " if has_kids else "  ")
            # Count what the folder can actually show, not what it once held.
            filed = [r for r, fid in assignments.items() if fid == ident]
            n_gone = sum(1 for r in filed if r not in sessions)
            n_ok = len(filed) - n_gone
            count = f"{n_ok} of {len(filed)}" if n_gone else str(n_ok)
            line = f"{tree}📁 {f.get('name', '(folder)')}  ({count})"
            attr = folder_attr(f.get("color", 0), selected=is_cursor)
        elif kind == "missing":
            indent = "  " * depth
            nm = custom_titles.get(ident) or ident[:8]
            line = f"{indent}  ✗ {nm}  ·  unavailable — transcript gone (del to unfile)"
            attr = (sel_attr if is_cursor else curses.A_DIM)
        elif kind == "pending":
            rec = pending.get(ident, {})
            indent = "  " * depth
            nm = rec.get("name") or "(unnamed fork)"
            line = f"{indent}  ⧗ {nm}  ·  pending fork — Enter to create"
            attr = (sel_attr if is_cursor else curses.A_DIM)
        else:
            meta = sessions.get(ident, {})
            line = render_row(ident, depth, has_kids, is_open, meta,
                              starred=(ident in stars), custom_titles=custom_titles)
            attr = sel_attr if is_cursor else curses.A_NORMAL
        safe_addstr(stdscr, 1 + i, 0, short(line, list_w).ljust(list_w)[:list_w], attr)

    for i in range(body_h):
        try:
            stdscr.addch(1 + i, list_w, curses.ACS_VLINE)
        except curses.error:
            pass

    if 0 <= cursor < len(rows) and rows[cursor][0] == "missing":
        sid = rows[cursor][1]
        detail_rows = [
            ("Thread:  ", custom_titles.get(sid) or "(untitled)"),
            ("Session: ", sid),
            ("Status:  ", "UNAVAILABLE"),
            (None, ""),
            (None, "This thread is still filed in the folder, but its"),
            (None, "transcript is no longer in ~/.claude/projects, so it"),
            (None, "cannot be opened, forked or searched."),
            (None, ""),
            (None, "Most likely it ran as a child/SDK session, which never"),
            (None, "persists its JSONL. See data/DEBUG-data-loss.md."),
            (None, ""),
            (None, "del = drop it from this folder"),
        ]
        for i, (label, val) in enumerate(detail_rows[:body_h]):
            row = 1 + i
            if label:
                safe_addstr(stdscr, row, detail_x, label, curses.A_BOLD)
                safe_addstr(stdscr, row, detail_x + len(label),
                            short(val, max(1, detail_w - len(label) - 1)))
            else:
                safe_addstr(stdscr, row, detail_x, short(val, max(1, detail_w - 1)))
    elif 0 <= cursor < len(rows) and rows[cursor][0] == "pending":
        rec = pending.get(rows[cursor][1], {})
        parent = rec.get("parent", "")
        detail_rows = [
            ("Pending: ", rec.get("name") or "(unnamed fork)"),
            ("Fork of: ", parent),
            (None, ""),
            (None, "This fork hasn't been created yet."),
            (None, ""),
            (None, "enter = create it now (launches claude --fork-session)"),
            (None, "r = rename    del = discard this pending fork"),
        ]
        for i, (label, val) in enumerate(detail_rows[:body_h]):
            row = 1 + i
            if label:
                safe_addstr(stdscr, row, detail_x, label, curses.A_BOLD)
                safe_addstr(stdscr, row, detail_x + len(label),
                            short(val, max(1, detail_w - len(label) - 1)))
            else:
                safe_addstr(stdscr, row, detail_x, short(val, max(1, detail_w - 1)))
    elif 0 <= cursor < len(rows) and rows[cursor][0] == "folder":
        fid = rows[cursor][1]
        f = folder_by_id.get(fid, {})
        members = [r for r, x in assignments.items() if x == fid]
        gone = [r for r in members if r not in sessions]
        detail_rows = [
            ("Folder:  ", f.get("name", "(folder)")),
            ("Threads: ", str(len(members) - len(gone))),
        ]
        if gone:
            detail_rows += [
                ("Missing: ", f"{len(gone)} (transcript no longer on disk)"),
                (None, ""),
                (None, "Missing threads show as ✗ rows. Their JSONL is gone from"),
                (None, "~/.claude/projects, so they cannot be opened or forked."),
                (None, "Press del on one to drop it from this folder."),
            ]
        detail_rows += [
            (None, ""),
            (None, "A visual group only — sessions are not moved on disk."),
            (None, ""),
            (None, "r = rename folder   del = delete folder (keeps sessions)"),
            (None, "m on a thread = move it here   m then 0 = unfile"),
        ]
        for i, (label, val) in enumerate(detail_rows[:body_h]):
            row = 1 + i
            if label:
                safe_addstr(stdscr, row, detail_x, label, curses.A_BOLD)
                safe_addstr(stdscr, row, detail_x + len(label),
                            short(val, max(1, detail_w - len(label) - 1)))
            else:
                safe_addstr(stdscr, row, detail_x, short(val, max(1, detail_w - 1)))
    elif 0 <= cursor < len(rows):
        sid = rows[cursor][1]
        meta = sessions.get(sid, {})
        detail_rows = []
        detail_rows.append(("Session:      ", sid))
        detail_rows.append(("TUI Title:    ", display_title(sid, meta, custom_titles)))
        detail_rows.append(("Session Name: ",
                            session_name_for(sid, meta) or "(unnamed — /rename in the session)"))
        detail_rows.append(("Project:      ", decode_project(meta.get("project"))))
        detail_rows.append(("CWD:          ", meta.get("cwd") or "(unknown)"))
        mt = meta.get("mtime")
        if mt:
            try:
                now = time.time()
                ago = now - mt
                if ago < 3600:
                    rel = f"{int(ago // 60)}m ago"
                elif ago < 86400:
                    rel = f"{int(ago // 3600)}h ago"
                else:
                    rel = f"{int(ago // 86400)}d ago"
                stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(mt))
                detail_rows.append(("Last used:    ", f"{stamp}  ({rel})"))
            except (ValueError, OSError):
                pass
        stats = meta.get("stats") or {}
        if stats:
            tu = stats.get("tool_uses", 0)
            ch = stats.get("total_chars", 0)
            # Prefer the authoritative API usage count (matches claude's /context).
            # Fall back to a rough chars/4 estimate only if usage isn't recorded.
            ctok = stats.get("current_tokens")
            est = ctok if ctok is not None else (ch // 4)
            if est >= 1_000_000:
                tokens_str = f"{est / 1_000_000:.2f}M tokens"
            elif est >= 1000:
                tokens_str = f"{est // 1000}k tokens"
            else:
                tokens_str = f"{est} tokens"
            pct = min(100, est * 100 // 1_000_000)
            if ch >= 1024 * 1024:
                size_str = f"{ch / (1024 * 1024):.1f}MB transcript"
            elif ch >= 1024:
                size_str = f"{ch // 1024}KB transcript"
            else:
                size_str = f"{ch}B transcript"
            suffix = "" if ctok is not None else "  (est)"
            detail_rows.append((
                "/context:     ",
                f"{tokens_str} ({pct}% of 1M){suffix}  ·  {size_str}  ·  {tu} tool calls",
            ))
        if meta.get("forked_from"):
            detached_note = "  (DETACHED — shown as its own root)" if sid in detached_ids else ""
            detail_rows.append(("Forked:       ",
                                f"from {meta['forked_from']}{detached_note}"))
        report_text = load_report_text(sid)
        if report_text:
            n_entries = report_text.count("## Report from")
            detail_rows.append((
                "Reports: ",
                f"{n_entries} entr{'y' if n_entries == 1 else 'ies'} "
                f"({len(report_text)} chars) — injected on continue/fork",
            ))
        detail_rows.append((None, ""))
        detail_rows.append((None, "Summary:"))
        for ln in wrap_text(meta.get("summary", ""), detail_w - 2):
            detail_rows.append((None, "  " + ln))
        detail_rows.append((None, ""))
        detail_rows.append((None, "Topics:"))
        for t in meta.get("topics", []):
            for ln in wrap_text("- " + t, detail_w - 2):
                detail_rows.append((None, "  " + ln))
        detail_rows.append((None, ""))
        detail_rows.append((None, "First prompt:"))
        for ln in wrap_text(meta.get("first_prompt", ""), detail_w - 2):
            detail_rows.append((None, "  " + ln))
        last = meta.get("last_prompt", "")
        if last and last != meta.get("first_prompt", ""):
            detail_rows.append((None, ""))
            detail_rows.append((None, "Last prompt:"))
            for ln in wrap_text(last, detail_w - 2):
                detail_rows.append((None, "  " + ln))

        terms = search_terms(search_query)
        if terms:
            # `/` also matches the transcript body, which this pane only shows
            # a slice of. Say so for any term that hit the session but isn't
            # visible here, so an apparently-unrelated result makes sense.
            visible = " ".join(str(v) for _, v in detail_rows).lower()
            body = (meta.get("searchable_text") or "").lower()
            unseen = [t for t in terms if t not in visible and t in body]
            if unseen:
                detail_rows.append((None, ""))
                quoted = ", ".join(f'"{t}"' for t in unseen)
                for ln in wrap_text(
                    f"⌕ {quoted} matched the transcript body, which is not "
                    f"shown above.", detail_w - 2):
                    detail_rows.append((None, ln))

        for i, (label, val) in enumerate(detail_rows[:body_h]):
            row = 1 + i
            if label:
                safe_addstr(stdscr, row, detail_x, label, curses.A_BOLD)
                addstr_hits(
                    stdscr, row, detail_x + len(label),
                    short(val, max(1, detail_w - len(label) - 1)), terms,
                )
            else:
                addstr_hits(stdscr, row, detail_x,
                            short(val, max(1, detail_w - 1)), terms)

    if status_msg:
        help_text = "  " + status_msg
    elif rename_active:
        help_text = "  rename mode: type name   enter=save   esc=cancel   blank+enter=clear"
    elif search_active:
        help_text = "  type to search   enter=confirm   esc=cancel   backspace=delete char"
    elif sem_query:
        help_text = "  ↑↓ move   enter=open   f=fork   esc=clear FAISS   q=quit"
    elif search_query:
        help_text = "  ↑↓ move   enter=continue   f=fork   s=star   r=rename   del=trash   q=quit"
    else:
        help_text = "  ↑↓ →/space  / search  w=FAISS  d=mkdir  m=move  s=star  r=rename  f=fork  x=detach  del=trash  v=saved  F5=rescan  enter=open  q=quit"
    counter = f"  {cursor + 1}/{len(rows)}  " if rows else "  0/0  "
    pad = w - len(help_text) - len(counter)
    if pad < 0:
        pad = 0
    safe_addstr(
        stdscr, h - 1, 0,
        (help_text + " " * pad + counter).ljust(w)[:w], curses.A_REVERSE,
    )
    stdscr.refresh()


def parent_of(sid, sessions, indexed_ids, detached=None):
    if detached and sid in detached:
        return None
    p = sessions.get(sid, {}).get("forked_from")
    if p in indexed_ids:
        return p
    return None


VENV_PY = DATA_DIR / "venv" / "bin" / "python"
SEMANTIC_SCRIPT = PLUGIN_DIR / "scripts" / "semantic.py"


def semantic_search(query, k=50):
    """Ordered session ids via local/Voyage FAISS, or None if unavailable."""
    q = (query or "").strip()
    if not q or not (VENV_PY.exists() and SEMANTIC_SCRIPT.exists()):
        return None
    try:
        res = subprocess.run(
            [str(VENV_PY), str(SEMANTIC_SCRIPT), "query", q, "-k", str(k)],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if res.returncode != 0 or not res.stdout.strip():
        return None
    try:
        hits = json.loads(res.stdout.strip())
    except json.JSONDecodeError:
        return None
    return [h["id"] for h in hits if isinstance(h, dict) and h.get("id")]


def tui_main(stdscr, sessions, roots, children):
    curses.curs_set(0)
    stdscr.keypad(True)
    init_colors()
    expanded = set()
    cursor = 0
    scroll = 0
    indexed_ids = set(sessions.keys())
    search_query = ""
    search_active = False
    wsearch_active = False
    wsearch_buffer = ""
    sem_query = ""
    sem_results = []
    stars = load_stars()
    custom_titles = load_titles()
    folders, assignments = load_folders()
    pending = load_pending()
    detached = load_detached()
    permissive = False  # session-scoped — don't persist a dangerous default
    rename_active = False
    rename_buffer = ""
    rename_sid = None
    rename_kind = "session"   # "session" or "folder"
    mkdir_active = False
    mkdir_buffer = ""
    move_active = False
    move_root = None          # root sid being filed
    status_msg = None         # transient one-shot hint shown in the help bar

    def rebuild():
        nonlocal roots, children
        roots, children = build_tree(sessions, detached)

    while True:
        if sem_query:
            rows = [("session", sid, 0, False, False)
                    for sid in sem_results if sid in sessions]
        elif search_query:
            rows = filtered_flat(sessions, search_query, custom_titles)
        else:
            rows = build_rows(roots, children, expanded, folders, assignments,
                              pending, known=set(sessions))

        if not rows and not search_active and not wsearch_active:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            safe_addstr(stdscr, 0, 0, " /browse-experts ".ljust(w)[:w], curses.A_REVERSE)
            _q = sem_query or search_query
            safe_addstr(stdscr, 2, 2, f"no sessions match: {_q}" if _q else "no sessions to display")
            safe_addstr(stdscr, 4, 2, "press / to edit search, esc to clear, q to quit")
            stdscr.refresh()
            key = stdscr.getch()
            if key in (ord("q"),):
                return None
            if key == 27:  # esc
                search_query = ""
                sem_query = ""
                continue
            if key == ord("/"):
                search_active = True
                continue
            continue

        if cursor >= len(rows):
            cursor = max(0, len(rows) - 1)
        if cursor < 0:
            cursor = 0

        h, _ = stdscr.getmaxyx()
        body_h = max(1, h - 2)
        if cursor < scroll:
            scroll = cursor
        elif cursor >= scroll + body_h:
            scroll = cursor - body_h + 1

        active_rename_buf = (
            mkdir_buffer if mkdir_active else rename_buffer
        )
        draw_status = status_msg
        if move_active:
            choices = "  ".join(f"{i+1}={f['name'][:12]}" for i, f in enumerate(folders[:9]))
            draw_status = f"move to: {choices}   0=unfile   esc=cancel"
        elif mkdir_active:
            draw_status = f"new folder name: {mkdir_buffer}_   enter=create   esc=cancel"
        elif wsearch_active:
            draw_status = f"FAISS search: {wsearch_buffer}_   enter=search   esc=cancel"
        draw(stdscr, sessions, rows, cursor, scroll, search_query, search_active,
             stars, permissive, custom_titles, rename_active,
             active_rename_buf, folders, assignments, draw_status, pending,
             detached, sem_query)
        status_msg = None  # one-shot

        try:
            key = stdscr.getch()
        except KeyboardInterrupt:
            return None

        # MakeDir-input mode: capture a new folder name.
        if mkdir_active:
            if key in (10, 13):
                name = mkdir_buffer.strip()
                if name:
                    color = len(folders) % n_folder_colors()
                    fid = new_folder_id()
                    folders.append({"id": fid, "name": name, "color": color})
                    save_folders(folders, assignments)
                    expanded.add(fid)
                mkdir_active = False
                mkdir_buffer = ""
            elif key == 27:
                mkdir_active = False
                mkdir_buffer = ""
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                mkdir_buffer = mkdir_buffer[:-1]
            elif 32 <= key < 127:
                mkdir_buffer += chr(key)
            continue

        # Move mode: pick a destination folder by number (0 = unfile).
        if move_active:
            if key == 27:
                move_active = False
                move_root = None
            elif key == ord("0"):
                assignments.pop(move_root, None)
                save_folders(folders, assignments)
                status_msg = "unfiled."
                move_active = False
                move_root = None
            elif ord("1") <= key <= ord("9"):
                n = key - ord("1")
                if n < len(folders):
                    assignments[move_root] = folders[n]["id"]
                    save_folders(folders, assignments)
                    expanded.add(folders[n]["id"])
                    status_msg = f"moved to {folders[n]['name']}."
                    move_active = False
                    move_root = None
            continue

        # Rename-input mode: capture text for a session OR folder name.
        if rename_active:
            if key in (10, 13):
                new_title = rename_buffer.strip()
                if rename_kind == "folder":
                    for f in folders:
                        if f["id"] == rename_sid:
                            if new_title:
                                f["name"] = new_title
                            break
                    save_folders(folders, assignments)
                elif rename_kind == "pending":
                    if rename_sid in pending:
                        pending[rename_sid]["name"] = new_title or "(unnamed fork)"
                        save_pending(pending)
                else:
                    if new_title:
                        custom_titles[rename_sid] = new_title
                    else:
                        custom_titles.pop(rename_sid, None)
                    save_titles(custom_titles)
                rename_active = False
                rename_buffer = ""
                rename_sid = None
            elif key == 27:
                rename_active = False
                rename_buffer = ""
                rename_sid = None
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                rename_buffer = rename_buffer[:-1]
            elif 32 <= key < 127:
                rename_buffer += chr(key)
            continue

        # Search-input mode: capture text until Enter or Esc.
        if search_active:
            if key in (10, 13):              # enter: confirm
                search_active = False
                cursor = 0
            elif key == 27:                  # esc: cancel edit (keep prior)
                search_active = False
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                search_query = search_query[:-1]
                cursor = 0
            elif 32 <= key < 127:            # printable
                search_query += chr(key)
                cursor = 0
            continue

        # FAISS-search-input mode: type a query, Enter runs semantic search.
        if wsearch_active:
            if key in (10, 13):
                res = semantic_search(wsearch_buffer)
                if res is None:
                    status_msg = "semantic search not set up (run setup_local.sh + semantic.py build)"
                else:
                    sem_results = [s for s in res if s in sessions]
                    sem_query = wsearch_buffer.strip()
                    search_query = ""
                    cursor = 0
                wsearch_active = False
            elif key == 27:
                wsearch_active = False
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                wsearch_buffer = wsearch_buffer[:-1]
            elif 32 <= key < 127:
                wsearch_buffer += chr(key)
            continue

        kind, ident, depth, has_kids, is_open = rows[cursor]
        is_folder = kind == "folder"
        is_pending = kind == "pending"
        is_missing = kind == "missing"

        if key == ord("q"):
            return None
        elif key == 27:                      # esc: clear filter or quit
            if sem_query or search_query:
                sem_query = ""
                search_query = ""
                cursor = 0
            else:
                return None
        elif key == ord("/"):
            sem_query = ""
            search_active = True
        elif key in (ord("w"), ord("W")):
            wsearch_active = True
            wsearch_buffer = ""
        elif key in (curses.KEY_DOWN, ord("j")):
            cursor = min(cursor + 1, len(rows) - 1)
        elif key in (curses.KEY_UP, ord("k")):
            cursor = max(cursor - 1, 0)
        elif key == curses.KEY_NPAGE:
            cursor = min(cursor + body_h, len(rows) - 1)
        elif key == curses.KEY_PPAGE:
            cursor = max(cursor - body_h, 0)
        elif key in (curses.KEY_HOME, ord("g")):
            cursor = 0
        elif key in (curses.KEY_END, ord("G")):
            cursor = len(rows) - 1
        elif key == ord("x"):               # x — detach / re-attach a child
            if is_folder or is_pending or is_missing:
                status_msg = "select a session to detach"
            elif ident in detached:
                detached.discard(ident)
                save_detached(detached)
                rebuild()
                status_msg = f"{ident[:8]} re-attached to its fork parent"
            elif not sessions.get(ident, {}).get("forked_from"):
                status_msg = "already a root — nothing to detach from"
            else:
                # View-level only: the transcript keeps its forkedFrom. The
                # node becomes its own root (keeping its own children) so it
                # can be filed separately from the tree it came out of.
                detached.add(ident)
                save_detached(detached)
                # Inherit the old tree's folder so it doesn't silently unfile.
                old_root = find_root(ident, sessions)
                if old_root in assignments and ident not in assignments:
                    assignments[ident] = assignments[old_root]
                    save_folders(folders, assignments)
                rebuild()
                rows2 = build_rows(roots, children, expanded, folders,
                                   assignments, pending, known=set(sessions))
                for i, r in enumerate(rows2):
                    if r[0] == "session" and r[1] == ident:
                        cursor = i
                        break
                status_msg = f"{ident[:8]} detached — now its own root, m to file it"
        elif key in (ord("v"), curses.KEY_F2):   # v / F2 — saved-chats panel
            picked = saved_panel(stdscr, load_saved())
            if picked:
                if picked not in sessions:
                    status_msg = f"{picked[:8]} is saved but not in the index — press F5 to rescan"
                else:
                    # Open every ancestor (and the enclosing folder) so the row
                    # is actually reachable, then put the cursor on it.
                    # Mirror build_tree's parent_of: a detached node is its own
                    # root, so stop walking there — otherwise we'd expand the
                    # original parent's branch, where the row no longer lives,
                    # and miss the detached node's own enclosing folder.
                    chain, cur, seen = [], picked, set()
                    while cur and cur not in seen:
                        seen.add(cur)
                        chain.append(cur)
                        if cur in detached:
                            break
                        nxt = sessions.get(cur, {}).get("forked_from")
                        cur = nxt if (nxt and nxt in sessions) else None
                    for anc in chain[1:]:
                        expanded.add(anc)
                    fid = assignments.get(chain[-1])
                    if fid:
                        expanded.add(fid)
                    search_query = ""
                    rows2 = build_rows(roots, children, expanded, folders,
                                       assignments, pending, known=set(sessions))
                    for i, r in enumerate(rows2):
                        if r[0] == "session" and r[1] == picked:
                            cursor = i
                            break
                    else:
                        status_msg = f"{picked[:8]} is indexed but not reachable in the tree"
        elif key in (curses.KEY_F5, 18):     # F5 / ctrl-r — force a rescan
            # Re-run the indexer, then reload every on-disk source. The index
            # is otherwise only refreshed by spawn_browse.sh at launch, so a
            # session finished in another window since then is invisible until
            # the next /browse-experts.
            keep = rows[cursor][1] if 0 <= cursor < len(rows) else None
            show_busy(stdscr, "  rescanning — running the indexer, this can take a minute…")
            try:
                proc = subprocess.run([sys.executable, str(INDEXER)],
                                      capture_output=True, text=True, timeout=1800)
                tail = (proc.stderr or proc.stdout or "").strip().splitlines()
                note = tail[-1] if tail else "done"
                ok = proc.returncode == 0
            except subprocess.TimeoutExpired:
                ok, note = False, "indexer timed out after 30 min"
            except OSError as e:
                ok, note = False, f"indexer failed to start: {e}"

            before = len(sessions)
            # Mutate in place: draw() and the closures hold this same dict.
            sessions.clear()
            sessions.update(load_index())
            indexed_ids = set(sessions)
            stars = load_stars()
            custom_titles = load_titles()
            folders, assignments = load_folders()
            pending = load_pending()
            detached = load_detached()
            rebuild()
            # Drop expand-state for rows that no longer exist.
            expanded.intersection_update(
                set(sessions) | {f["id"] for f in folders} | set(pending)
            )
            rows2 = build_rows(roots, children, expanded, folders, assignments,
                               pending, known=set(sessions))
            cursor = min(cursor, max(0, len(rows2) - 1))
            if keep:
                for i, r in enumerate(rows2):
                    if r[1] == keep:
                        cursor = i
                        break
            scroll = min(scroll, cursor)
            delta = len(sessions) - before
            if ok:
                change = f"+{delta}" if delta > 0 else (str(delta) if delta else "no change")
                status_msg = f"rescan: {note} [{change}]"
            else:
                status_msg = f"rescan FAILED: {note}"
        elif key in (curses.KEY_RIGHT, ord("l"), ord(" ")):
            if not search_query and has_kids and not is_open:
                expanded.add(ident)
            elif not search_query and has_kids and is_open:
                cursor = min(cursor + 1, len(rows) - 1)
        elif key in (curses.KEY_LEFT, ord("h")):
            if not search_query and has_kids and is_open:
                expanded.discard(ident)
            elif not search_query and not is_folder and not is_missing:
                parent = parent_of(ident, sessions, indexed_ids, detached)
                if parent:
                    for i, r in enumerate(rows):
                        if r[0] == "session" and r[1] == parent:
                            cursor = i
                            break
        elif key == ord("d"):
            # MakeDir — name a new visual folder
            mkdir_active = True
            mkdir_buffer = ""
        elif key == ord("m") and not is_folder and not is_pending and not is_missing:
            # Move the selected session's whole tree into a folder
            if folders:
                move_active = True
                move_root = find_root(ident, sessions, detached)
            else:
                status_msg = "no folders yet — press d to make one first"
        elif key in (curses.KEY_ENTER, 10, 13):
            if is_folder:
                # toggle expand
                if ident in expanded:
                    expanded.discard(ident)
                else:
                    expanded.add(ident)
            elif is_missing:
                status_msg = "transcript is gone — nothing to open (del to unfile)"
            elif is_pending:
                # Create the fork NOW: launch claude --resume <parent>
                # --fork-session natively. main() detects the new fork id,
                # transfers this placeholder's name to it, and clears the
                # placeholder.
                rec = pending.get(ident, {})
                parent_sid = rec.get("parent")
                # Carry the PARENT's index entry through, exactly like the
                # continue branch below. main() needs `project`/`cwd` from it to
                # chdir into the parent's project dir before launching: `claude
                # --resume <id>` only resolves ids inside the project dir of the
                # current cwd, so without this the fork ran from wherever
                # /browse-experts was invoked and died with "No conversation
                # found with session ID".
                return sessions.get(parent_sid, {}) | {
                    "_sid": parent_sid,
                    "_mode": "fork",
                    "_permissive": permissive,
                    "_pending_id": ident,
                    "_pending_name": rec.get("name") or "",
                }
            else:
                return sessions[ident] | {"_sid": ident, "_mode": "continue", "_permissive": permissive}
        elif key in (ord("f"), ord("F")):
            if is_folder or is_pending or is_missing:
                status_msg = "select a real session to fork"
                continue
            # Create a PLACEHOLDER pending fork under this session, then drop
            # into rename mode so it can be named before it's created. The fork
            # is materialized natively only when the user presses Enter on the
            # placeholder (see the Enter handler) — headless `-p` forks don't
            # persist on interactive resume.
            pid = new_pending_id()
            pending[pid] = {"parent": ident, "name": ""}
            save_pending(pending)
            expanded.add(ident)   # reveal the placeholder under its parent
            rows2 = build_rows(roots, children, expanded, folders, assignments,
                               pending, known=set(sessions))
            for i, r in enumerate(rows2):
                if r[0] == "pending" and r[1] == pid:
                    cursor = i
                    break
            rename_active = True
            rename_kind = "pending"
            rename_sid = pid
            rename_buffer = ""
        elif key == curses.KEY_DC:
            if is_missing:
                # Nothing to trash — the JSONL is already gone. Just drop the
                # stale folder assignment so the count stops claiming it.
                # Keep the custom title and star: if the transcript is ever
                # recovered from data/*-backups the thread comes back named.
                assignments.pop(ident, None)
                save_folders(folders, assignments)
                cursor = max(0, cursor - 1)
                status_msg = f"unfiled {ident[:8]} (transcript was already gone)"
                continue
            if is_pending:
                pending.pop(ident, None)
                save_pending(pending)
                cursor = max(0, cursor - 1)
                status_msg = "pending fork discarded"
                continue
            if is_folder:
                f = next((x for x in folders if x["id"] == ident), {})
                n = sum(1 for v in assignments.values() if v == ident)
                if confirm_prompt(stdscr, f"  delete folder '{f.get('name','')[:30]}' ({n} thread(s) will be unfiled)? [y/N]"):
                    folders[:] = [x for x in folders if x["id"] != ident]
                    for k in [k for k, v in assignments.items() if v == ident]:
                        assignments.pop(k, None)
                    save_folders(folders, assignments)
                    expanded.discard(ident)
                    cursor = max(0, cursor - 1)
                continue
            sid = ident
            current_title = display_title(sid, sessions.get(sid, {}), custom_titles)
            n_children = len(children.get(sid, []))
            if n_children:
                warn = (f"  ⚠ '{current_title[:40]}' is the PARENT of {n_children} fork(s) — "
                        f"deleting orphans them. Delete anyway? [y/N]")
            else:
                warn = f"  delete '{current_title[:50]}' (moves JSONL to trash)? [y/N]"
            if confirm_prompt(stdscr, warn):
                ok, msg = trash_session(sid, sessions)
                if ok:
                    custom_titles.pop(sid, None)
                    save_titles(custom_titles)
                    if sid in stars:
                        stars.discard(sid)
                        save_stars(stars)
                    assignments.pop(sid, None)
                    save_folders(folders, assignments)
                    indexed_ids.discard(sid)
                    rebuild()
                    cursor = max(0, cursor - 1)
                    show_busy(stdscr, f"  deleted — {msg}")
                else:
                    show_busy(stdscr, f"  delete failed: {msg} — press any key")
                    stdscr.getch()
        elif key in (ord("s"), ord("S")):
            if is_folder or is_pending or is_missing:
                continue
            if ident in stars:
                stars.discard(ident)
            else:
                stars.add(ident)
            save_stars(stars)
        elif key in (ord("p"), ord("P")):
            permissive = not permissive
        elif key in (ord("r"), ord("R")):
            if is_missing:
                status_msg = "can't rename an unavailable thread"
                continue
            rename_active = True
            rename_sid = ident
            if is_folder:
                rename_kind = "folder"
                f = next((x for x in folders if x["id"] == ident), {})
                rename_buffer = f.get("name", "")
            elif is_pending:
                rename_kind = "pending"
                rename_buffer = pending.get(ident, {}).get("name", "")
            else:
                rename_kind = "session"
                rename_buffer = display_title(ident, sessions.get(ident, {}), custom_titles)
                if rename_buffer == "(untitled)":
                    rename_buffer = ""


def main():
    sessions = load_index()
    if not sessions:
        print("No sessions indexed yet. Run /ask-expert first, or:")
        print(f"  python3 {PLUGIN_DIR / 'scripts' / 'index_sessions.py'}")
        sys.exit(1)

    roots, children = build_tree(sessions, load_detached())

    try:
        selected = curses.wrapper(tui_main, sessions, roots, children)
    except KeyboardInterrupt:
        selected = None

    if not selected:
        print("cancelled.")
        sys.exit(0)

    sid = selected["_sid"]
    mode = selected.get("_mode", "continue")
    if find_transcript(sid) is None:
        print(f"\n✋ no transcript for {sid[:8]} under ~/.claude/projects — "
              f"nothing to resume.")
        sys.exit(1)
    launch_dir = resolve_launch_dir(
        sid, selected.get("project"), selected.get("cwd")
    )
    if launch_dir:
        try:
            os.chdir(launch_dir)
            print(f"cwd → {launch_dir}")
        except OSError:
            pass

    guard = str(GUARD)

    # HARD BLOCK: never continue the session that launched this TUI — that
    # would put the same id in two claude processes and clobber the transcript
    # on exit. (Forking is always safe: it mints a new id.)
    if mode != "fork" and CURRENT_SESSION and sid == CURRENT_SESSION:
        print(f"\n✋ {sid[:8]} is the session you launched this browser FROM.")
        print("Continuing it would open it twice and overwrite your history on exit.")
        print("Switch back to that window instead, or FORK it (press f) to branch safely.")
        sys.exit(0)

    # Concurrent-open guard (continue only). Checks both a live lock written by
    # other plugin-launched resumes AND a best-effort cmdline scan.
    if mode != "fork":
        conflict = subprocess.run(["python3", guard, "check-open", sid])
        if conflict.returncode == 0:
            print(f"\n⚠  session {sid[:8]} is ALREADY OPEN in another window (see above).")
            print("Opening it here too can make the two windows overwrite each other's transcript.")
            try:
                ans = input("open anyway? [y/N] ").strip().lower()
            except EOFError:
                ans = ""
            if ans not in ("y", "yes"):
                print("cancelled.")
                sys.exit(0)

    # Snapshot the transcript before resuming so any rollback is recoverable.
    subprocess.run(["python3", guard, "snapshot", sid])

    # For a fork, mint the new session id OURSELVES via --session-id, so we
    # know it exactly and can record lineage deterministically. Native
    # --fork-session forks don't write `forkedFrom` metadata, so after-the-fact
    # detection can't reliably nest them — this avoids guessing entirely.
    fork_id = None
    if mode == "fork":
        import uuid
        fork_id = str(uuid.uuid4())
        print(f"forking session {sid} -> {fork_id[:8]} ...")
        cmd = ["claude", "--resume", sid, "--fork-session", "--session-id", fork_id]
    else:
        print(f"continuing session {sid} ...")
        cmd = ["claude", "--resume", sid]

    if selected.get("_permissive"):
        cmd.append("--dangerously-skip-permissions")
        print("⚠  --dangerously-skip-permissions enabled")

    report_text = load_report_text(sid)
    if report_text:
        appended = (
            "# Reports from sibling forks of this session\n\n"
            "The following are summaries written via /report-back by other "
            "Claude sessions that were forked from this same parent. Treat them "
            "as authoritative updates about work that has happened since this "
            "session's transcript was recorded.\n\n"
            f"{report_text}"
        )
        cmd += ["--append-system-prompt", appended]
        print(
            f"injecting {len(report_text)} chars of sibling reports via "
            f"--append-system-prompt"
        )

    # Reminder: Ctrl-C abort skips the transcript flush for resumed sessions,
    # so work is lost. Clean exit (Ctrl-D / /exit) saves.
    print("\033[1;33m⚠  To SAVE your work, exit with Ctrl-D or /exit — "
          "NOT Ctrl-C Ctrl-C (that aborts before saving).\033[0m")

    # DEBUG: log exactly what we launch + the pre/post state of the session
    # file, so we can see whether the interactive run actually writes to it.
    try:
        dbg = DATA_DIR / "launch-debug.log"
        live = next(Path.home().glob(f".claude/projects/*/{sid}.jsonl"), None)
        pre = live.stat().st_size if live else -1
        with dbg.open("a") as fh:
            fh.write(f"\n=== {time.strftime('%H:%M:%S')} LAUNCH ===\n")
            fh.write(f"sid={sid} mode={mode}\n")
            fh.write(f"cwd={os.getcwd()}\n")
            fh.write(f"jsonl={live} pre_size={pre}\n")
            fh.write(f"cmd={cmd}\n")
        import atexit
        def _post():
            try:
                post = live.stat().st_size if live else -1
                with dbg.open("a") as fh:
                    fh.write(f"--- {time.strftime('%H:%M:%S')} EXIT sid={sid} post_size={post} (delta={post-pre})\n")
            except Exception:
                pass
        atexit.register(_post)
    except Exception:
        pass

    # For a native fork, snapshot existing session ids so we can detect the new
    # fork file claude creates and register it after the session ends.
    def _all_session_ids():
        return {p.stem for p in Path.home().glob(".claude/projects/*/*.jsonl")}
    fork_before = _all_session_ids() if mode == "fork" else set()

    # CRITICAL: launch claude with a CLEAN environment. This TUI runs as a
    # subprocess of a claude session, so CLAUDE_CODE_ENTRYPOINT=sdk-cli and
    # CLAUDE_CODE_CHILD_SESSION=1 are in our env — and they LEAK into the
    # claude we spawn, marking it a child/SDK session that does NOT persist its
    # transcript to disk. Stripping them makes the spawned claude a normal
    # interactive `cli` session that saves normally.
    clean_env = {
        k: v for k, v in os.environ.items()
        if k not in (
            "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_CHILD_SESSION",
            "CLAUDE_CODE_SESSION_ID", "CLAUDECODE", "CLAUDE_CODE_EXECPATH",
            "CLAUDE_CODE_SSE_PORT", "CLAUDE_CODE_API_KEY_HELPER_TTL_MS",
            "AI_AGENT",
        )
    }
    # Belt-and-suspenders against the child/SDK env leak that stops resumed
    # sessions persisting their transcript: in addition to stripping the leaked
    # markers above, explicitly force persistence on. Equivalent to prefixing
    # the launch with CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1 claude ...
    clean_env["CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"] = "1"

    # Hold a lock for the resumed id while claude runs so other plugin-launched
    # windows refuse to open it concurrently. Forks get a fresh id from claude,
    # so locking the parent id would be wrong — only lock on continue.
    lock_id = sid if mode != "fork" else None
    if lock_id:
        subprocess.run(["python3", guard, "lock", lock_id])
    try:
        rc = subprocess.run(cmd, env=clean_env).returncode
    finally:
        if lock_id:
            subprocess.run(["python3", guard, "unlock", lock_id])

    # Register the fork DETERMINISTICALLY. Because we passed --session-id
    # <fork_id>, we know the fork's id exactly — no detection/guessing. Record
    # lineage (fork_id -> parent) so it nests under the parent, carry the
    # placeholder's name onto it, and clear the placeholder.
    if mode == "fork" and fork_id:
        pending_id = selected.get("_pending_id")
        pending_name = (selected.get("_pending_name") or "").strip()
        if pending_id:
            try:
                pend = load_pending()
                if pending_id in pend:
                    pend.pop(pending_id, None)
                    save_pending(pend)
            except Exception:
                pass
        fork_jsonl = next(Path.home().glob(f".claude/projects/*/{fork_id}.jsonl"), None)
        if fork_jsonl is None:
            print("note: fork wasn't created (no turn taken, or launch cancelled) — "
                  "nothing to register.")
        else:
            try:
                # Record lineage first (authoritative; survives reindex).
                lin = {}
                if LINEAGE_FILE.exists():
                    try:
                        lin = json.loads(LINEAGE_FILE.read_text())
                    except (json.JSONDecodeError, OSError):
                        lin = {}
                lin[fork_id] = sid
                LINEAGE_FILE.write_text(json.dumps(lin, indent=2))
                # Add to the index now and force the parent link (it won't carry
                # forkedFrom metadata, so set forked_from explicitly).
                sessions_idx = load_index()
                add_fork_to_index(fork_id, sessions_idx)
                if fork_id in sessions_idx:
                    sessions_idx[fork_id]["forked_from"] = sid
                    save_index(sessions_idx)
                if pending_name:
                    titles = load_titles()
                    titles[fork_id] = pending_name
                    save_titles(titles)
                label = pending_name or fork_id[:8]
                print(f"registered fork '{label}' ({fork_id[:8]}) nested under {sid[:8]}.")
            except Exception as e:
                print(f"(could not register fork {fork_id[:8]}: {e})")
    sys.exit(rc)


if __name__ == "__main__":
    main()
