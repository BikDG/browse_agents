#!/usr/bin/env python3
"""Given a user question, find the past sessions most likely to be relevant.

Reads data/index.json (built by index_sessions.py), asks Haiku to rerank
the catalog against the user's question, and prints the top matches in a
format the /ask-expert slash command can present.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
INDEX_FILE = PLUGIN_DIR / "data" / "index.json"
VENV_PY = PLUGIN_DIR / "data" / "venv" / "bin" / "python"
SEMANTIC = PLUGIN_DIR / "scripts" / "semantic.py"
RECALL_K = 25  # how many FAISS candidates to hand the Haiku reranker


def semantic_recall(question, k=RECALL_K):
    """Return an ordered list of candidate session ids via Voyage+FAISS, or None
    if semantic search isn't set up / fails (caller then uses the full catalog)."""
    if not (VENV_PY.exists() and SEMANTIC.exists()):
        return None
    try:
        res = subprocess.run(
            [str(VENV_PY), str(SEMANTIC), "query", question, "-k", str(k)],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if res.returncode != 0 or not res.stdout.strip():
        if res.stderr.strip():
            print(f"(semantic recall unavailable: {res.stderr.strip().splitlines()[-1][:160]})",
                  file=sys.stderr)
        return None
    try:
        hits = json.loads(res.stdout.strip())
    except json.JSONDecodeError:
        return None
    ids = [h["id"] for h in hits if isinstance(h, dict) and h.get("id")]
    return ids or None


def call_haiku(prompt, timeout=60):
    try:
        result = subprocess.run(
            ["claude", "-p", "--model", "haiku", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        print(f"claude -p failed: {result.stderr[:300]}", file=sys.stderr)
        return None
    return result.stdout.strip()


def strip_fences(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def main():
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        print("ERROR: no question provided", file=sys.stderr)
        sys.exit(1)
    if not INDEX_FILE.exists():
        print("ERROR: index not built — run index_sessions.py first", file=sys.stderr)
        sys.exit(1)

    try:
        index = json.loads(INDEX_FILE.read_text())
    except json.JSONDecodeError:
        print("ERROR: index.json is corrupt", file=sys.stderr)
        sys.exit(1)

    sessions = index.get("sessions", {})
    if not sessions:
        print("NO_MATCHES")
        return

    # Recall stage: if Voyage+FAISS is set up, narrow the catalog to the top-K
    # semantically-nearest sessions before the (expensive) Haiku rerank.
    # Otherwise fall back to the full catalog (original behaviour).
    recall_ids = semantic_recall(question)
    if recall_ids:
        ordered = [sid for sid in recall_ids if sid in sessions]
        print(f"(semantic recall: {len(ordered)} candidates via FAISS)", file=sys.stderr)
    else:
        ordered = list(sessions.keys())

    catalog = []
    for sid in ordered:
        meta = sessions.get(sid, {})
        catalog.append(
            {
                "id": sid,
                "title": meta.get("title") or "(untitled)",
                "summary": meta.get("summary", ""),
                "topics": meta.get("topics", []),
            }
        )

    prompt = (
        f"User's new question: {question}\n\n"
        f"Past Claude Code sessions (JSON):\n{json.dumps(catalog, indent=2)}\n\n"
        "Pick up to 10 sessions whose summary/topics make them genuinely useful "
        "as expert context for the new question. Match on topic specificity, not "
        "generic words. If none are clearly relevant, return an empty list.\n\n"
        'Output ONLY JSON: {"matches": [{"id": "...", "score": 0-100, '
        '"why": "one-line reason"}]}'
    )

    raw = call_haiku(prompt)
    if not raw:
        print("ERROR: reranker call failed", file=sys.stderr)
        sys.exit(1)
    raw = strip_fences(raw)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        print(f"ERROR: no JSON in reranker output: {raw[:300]}", file=sys.stderr)
        sys.exit(1)
    try:
        ranked = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        print(f"ERROR: reranker JSON parse: {e}\n{raw[:300]}", file=sys.stderr)
        sys.exit(1)

    matches = ranked.get("matches", [])
    # Drop low-confidence matches
    matches = [m for m in matches if m.get("score", 0) >= 40]
    matches.sort(key=lambda m: -m.get("score", 0))

    if not matches:
        print("NO_MATCHES")
        return

    for i, m in enumerate(matches[:10], start=1):
        sid = m.get("id", "")
        meta = sessions.get(sid, {})
        print(f"--- MATCH #{i} ---")
        print(f"session_id: {sid}")
        print(f"score: {m.get('score', 0)}")
        print(f"title: {meta.get('title') or '(untitled)'}")
        print(f"project: {meta.get('project', '')}")
        print(f"cwd: {meta.get('cwd', '')}")
        print(f"summary: {meta.get('summary', '')}")
        print(f"topics: {', '.join(meta.get('topics', []))}")
        print(f"why: {m.get('why', '')}")
        print()


if __name__ == "__main__":
    main()
