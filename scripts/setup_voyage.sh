#!/usr/bin/env bash
# One-time setup for ask-expert semantic search (Voyage embeddings + FAISS).
#   - creates a dedicated venv at data/venv (Ubuntu PEP-668 blocks pip --user)
#   - installs numpy, faiss-cpu, voyageai
#   - stores the Voyage API key at data/voyage.env (chmod 600); env var wins
#   - writes data/semantic/config.json (model) and validates with a test embed
#
# Usage:
#   bash setup_voyage.sh                 # prompts for the key (or uses $VOYAGE_API_KEY)
#   VOYAGE_API_KEY=pa-... bash setup_voyage.sh
#   bash setup_voyage.sh --model voyage-3   # override the default model
set -euo pipefail

SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
PLUGIN="$(dirname "$SCRIPTS")"
DATA="$PLUGIN/data"
VENV="$DATA/venv"
SEM="$DATA/semantic"
KEYFILE="$DATA/voyage.env"
MODEL="voyage-3.5-lite"

while [ $# -gt 0 ]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$SEM"

echo "== 1/4  creating venv at $VENV =="
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip >/dev/null

echo "== 2/4  installing numpy + faiss-cpu + voyageai (this can take a minute) =="
"$VENV/bin/pip" install --quiet numpy faiss-cpu voyageai

echo "== 3/4  API key =="
KEY="${VOYAGE_API_KEY:-}"
if [ -z "$KEY" ] && [ -f "$KEYFILE" ]; then
  KEY="$(grep -E '^VOYAGE_API_KEY=' "$KEYFILE" | head -1 | cut -d= -f2-)"
fi
if [ -z "$KEY" ]; then
  # read -s so the key isn't echoed to the terminal
  read -r -s -p "  Paste your Voyage API key (https://dash.voyageai.com): " KEY
  echo
fi
if [ -z "$KEY" ]; then
  echo "  no key provided — aborting." >&2; exit 1
fi
umask 077
printf 'VOYAGE_API_KEY=%s\n' "$KEY" > "$KEYFILE"
chmod 600 "$KEYFILE"
echo "  saved to $KEYFILE (chmod 600). NOTE: this is plaintext on disk."

printf '{\n  "model": "%s"\n}\n' "$MODEL" > "$SEM/config.json"
echo "  model: $MODEL  ($SEM/config.json)"

echo "== 4/4  validating (test embed) =="
if "$VENV/bin/python" "$SCRIPTS/semantic.py" check; then
  echo
  echo "Setup complete. Next:"
  echo "  $VENV/bin/python $SCRIPTS/semantic.py build        # embed all sessions"
  echo "  (or just run /browse-experts — incremental build runs automatically if present)"
else
  echo
  echo "Validation failed. Fix the key or edit $SEM/config.json (model), then re-run:" >&2
  echo "  $VENV/bin/python $SCRIPTS/semantic.py check" >&2
  exit 1
fi
