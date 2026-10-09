#!/usr/bin/env bash
# Interactive autoconfig for the ask-expert plugin (browse_agents).
#
# Detects an existing Claude Code install + any prior plugin/data dirs, then
# lets you choose: where runtime data lives, which terminal to spawn, whether to
# enable semantic search (local FAISS or Voyage API), and whether to add the
# `cont` alias. Writes ~/.config/ask-expert/config.env (which the plugin reads;
# a real env var always overrides it). Nothing here edits the plugin's code.
#
# Run it from the repo:  bash scripts/install.sh
set -u

SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(dirname "$SCRIPTS")"
CFG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/ask-expert"
CFG="$CFG_DIR/config.env"

say()  { printf '%s\n' "$*"; }
hr()   { printf -- '----------------------------------------\n'; }
ask()  { # ask "prompt" "default" -> echoes answer
  local p="$1" d="${2:-}" a
  if [ -n "$d" ]; then read -r -p "$p [$d]: " a; else read -r -p "$p: " a; fi
  printf '%s' "${a:-$d}"
}
yesno() { # yesno "prompt" "Y|N"(default) -> returns 0 for yes
  local p="$1" d="${2:-N}" a
  read -r -p "$p [$([ "$d" = Y ] && echo 'Y/n' || echo 'y/N')]: " a
  a="${a:-$d}"; case "$a" in y|Y|yes|YES) return 0;; *) return 1;; esac
}

say "browse_agents / ask-expert — setup"; hr

# --- 1. detect Claude Code + plugin/data dirs -----------------------------
CLAUDE_HOME="$HOME/.claude"
if [ -d "$CLAUDE_HOME" ]; then
  say "Found Claude Code at $CLAUDE_HOME"
else
  say "WARNING: $CLAUDE_HOME not found — is Claude Code installed? Continuing anyway."
fi
[ -d "$CLAUDE_HOME/plugins" ]              && say "  plugins dir:      $CLAUDE_HOME/plugins"
[ -d "$CLAUDE_HOME/plugins/marketplaces" ] && say "  marketplaces:     $CLAUDE_HOME/plugins/marketplaces"
command -v claude >/dev/null 2>&1 && say "  claude on PATH:   $(command -v claude)" || say "  claude NOT on PATH"

# candidate pre-existing data dirs (prior installs)
DEFAULT_DATA="${XDG_DATA_HOME:-$HOME/.local/share}/ask-expert"
FOUND=()
[ -f "$DEFAULT_DATA/index.json" ] && FOUND+=("$DEFAULT_DATA")
while IFS= read -r d; do FOUND+=("$d"); done < <(
  find "$CLAUDE_HOME/plugins" -maxdepth 4 -type d -name data 2>/dev/null \
    -exec sh -c '[ -f "$1/index.json" ] && echo "$1"' _ {} \;
)
if [ "${#FOUND[@]}" -gt 0 ]; then
  say; say "Existing ask-expert data detected:"; for d in "${FOUND[@]}"; do say "  - $d"; done
fi
hr

# --- 2. data dir ----------------------------------------------------------
say "Where should runtime data live (session index, folders, embeddings, backups)?"
say "Keeping it OUTSIDE the plugin dir means reinstalls won't wipe it."
DATA_DIR="$(ask "  data dir" "${FOUND[0]:-$DEFAULT_DATA}")"
DATA_DIR="${DATA_DIR/#\~/$HOME}"
mkdir -p "$DATA_DIR"
hr

# --- 3. terminal ----------------------------------------------------------
say "Terminal for spawning session windows (blank = auto-detect: tmux > gnome > ...)."
TERM_CMD="$(ask "  terminal command (optional)" "")"
hr

# --- 4. semantic search ---------------------------------------------------
say "Semantic session search (optional):"
say "  1) off     — keyword + LLM ranking only (no extra deps)"
say "  2) local   — FAISS + sentence-transformers, free & offline (downloads ~a few hundred MB)"
say "  3) voyage  — Voyage API (needs an API key)"
SEM_CHOICE="$(ask "  choose 1/2/3" "1")"
SEMANTIC="off"
case "$SEM_CHOICE" in
  2) SEMANTIC="local" ;;
  3) SEMANTIC="voyage" ;;
esac
hr

# --- 5. cont alias --------------------------------------------------------
ADD_ALIAS=no
if yesno "Add the 'cont' alias (quick-launch /browse-experts) to your shell rc?" "Y"; then
  ADD_ALIAS=yes
fi
hr

# --- 6. write config.env --------------------------------------------------
mkdir -p "$CFG_DIR"
{
  echo "# ask-expert config (written by install.sh). Real env vars override these."
  echo "ASK_EXPERT_DATA_DIR=$DATA_DIR"
  [ -n "$TERM_CMD" ] && echo "ASK_EXPERT_TERMINAL=$TERM_CMD"
  echo "ASK_EXPERT_SEMANTIC=$SEMANTIC"
} > "$CFG"
say "Wrote $CFG"

# add alias by sourcing the repo snippet from the user's rc (idempotent)
if [ "$ADD_ALIAS" = yes ]; then
  RC="$HOME/.bashrc"; [ -n "${ZSH_VERSION:-}" ] && RC="$HOME/.zshrc"
  LINE="[ -f \"$PLUGIN_ROOT/shell/ask-expert.aliases.sh\" ] && . \"$PLUGIN_ROOT/shell/ask-expert.aliases.sh\"  # ask-expert"
  if ! grep -qF "ask-expert.aliases.sh" "$RC" 2>/dev/null; then
    printf '\n%s\n' "$LINE" >> "$RC"
    say "Added alias sourcing to $RC (restart your shell or: source $RC)"
  else
    say "Alias already present in $RC"
  fi
fi
hr

# --- 7. run the chosen semantic setup -------------------------------------
if [ "$SEMANTIC" = local ]; then
  say "Setting up local semantic search ..."
  ASK_EXPERT_DATA_DIR="$DATA_DIR" bash "$SCRIPTS/setup_local.sh"
elif [ "$SEMANTIC" = voyage ]; then
  say "Setting up Voyage semantic search ..."
  ASK_EXPERT_DATA_DIR="$DATA_DIR" bash "$SCRIPTS/setup_voyage.sh"
fi
hr

# --- 8. next steps --------------------------------------------------------
say "Done. To install the plugin in Claude Code, run these inside claude:"
say "    /plugin marketplace add BikDG/browse_agents"
say "    /plugin install ask-expert@browse-agents"
say "  or for development:  claude --plugin-dir $PLUGIN_ROOT"
say
say "Config:   $CFG"
say "Data dir: $DATA_DIR"
[ "$SEMANTIC" != off ] && say "After install, embed sessions:  $DATA_DIR/venv/bin/python $SCRIPTS/semantic.py build"
