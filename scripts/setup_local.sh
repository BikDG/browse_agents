#!/usr/bin/env bash
# One-time setup for ask-expert LOCAL semantic search (free, offline).
#   - venv at data/venv (Ubuntu PEP-668 blocks pip --user)
#   - installs numpy, faiss-cpu, CPU-only torch, sentence-transformers
#   - writes data/semantic/config.json (provider=local, model)
#   - downloads + validates the model with a test embed
#
# Usage:
#   bash setup_local.sh                               # default model bge-small
#   bash setup_local.sh --model sentence-transformers/all-MiniLM-L6-v2
set -euo pipefail

SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
PLUGIN="$(dirname "$SCRIPTS")"
source "$SCRIPTS/paths.sh"
DATA="$DATA_DIR"
VENV="$DATA/venv"
SEM="$DATA/semantic"
MODEL="BAAI/bge-small-en-v1.5"

while [ $# -gt 0 ]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$SEM"

echo "== 1/4  creating venv at $VENV =="
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip >/dev/null

echo "== 2/4  installing numpy + faiss-cpu =="
"$VENV/bin/pip" install --quiet numpy faiss-cpu

echo "== 3/4  installing CPU-only torch + sentence-transformers (a few hundred MB) =="
# CPU-only torch keeps the download small and avoids CUDA-version mismatches;
# embedding a few hundred short docs doesn't need the GPU.
"$VENV/bin/pip" install --quiet torch --index-url https://download.pytorch.org/whl/cpu
"$VENV/bin/pip" install --quiet sentence-transformers

printf '{\n  "provider": "local",\n  "model": "%s"\n}\n' "$MODEL" > "$SEM/config.json"
echo "  provider: local   model: $MODEL   ($SEM/config.json)"

echo "== 4/4  downloading + validating the model (test embed) =="
if "$VENV/bin/python" "$SCRIPTS/semantic.py" check; then
  echo
  echo "Setup complete (local, free, offline). Next:"
  echo "  $VENV/bin/python $SCRIPTS/semantic.py build     # embed all sessions (status bar)"
  echo "  (or just run /browse-experts — incremental embed runs automatically)"
else
  echo
  echo "Validation failed. Check the model name in $SEM/config.json, then re-run:" >&2
  echo "  $VENV/bin/python $SCRIPTS/semantic.py check" >&2
  exit 1
fi
