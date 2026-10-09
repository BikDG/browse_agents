#!/usr/bin/env bash
# Launch the /browse-experts TUI.
#
# Resolution order:
#   1. tmux: open a new window in the current tmux session (works over SSH)
#   2. GNU screen: open a new window in the current screen session
#   3. Graphical terminal emulator: gnome-terminal / xfce4 / alacritty / ...
#   4. Text fallback: emit a markdown table of sessions to stdout and exit 2
#      (the slash command body switches into in-conversation selection mode)
#
# The script always prints a single status line on stdout so the calling slash
# command can detect the mode. Exit code:
#   0 = launched in a new window
#   2 = no display; printed text fallback for caller to render
#   1 = unexpected failure
set -u

TUI=/home/bik/.claude/plugins/ask-expert/scripts/browse_tui.py
LIST=/home/bik/.claude/plugins/ask-expert/scripts/list_sessions.py
INDEXER=/home/bik/.claude/plugins/ask-expert/scripts/index_sessions.py
SEMANTIC=/home/bik/.claude/plugins/ask-expert/scripts/semantic.py
VENV_PY=/home/bik/.claude/plugins/ask-expert/data/venv/bin/python

# Refresh the index, then (only if semantic search has been set up via
# setup_voyage.sh) incrementally embed any new/changed sessions. Both run in the
# background so the TUI launches immediately; the embed step no-ops otherwise.
export INDEXER SEMANTIC VENV_PY
nohup bash -c 'python3 "$INDEXER"; if [ -x "$VENV_PY" ] && [ -f "$SEMANTIC" ]; then "$VENV_PY" "$SEMANTIC" build; fi' >/dev/null 2>&1 &

# Ensure the periodic transcript-backup daemon is running (idempotent).
bash /home/bik/.claude/plugins/ask-expert/scripts/run_backup_daemon.sh >/dev/null 2>&1 || true

# Capture the id of the session launching this TUI so the TUI can hard-refuse
# to "continue" the very session you're sitting in (the #1 way two claude
# processes end up on one id and clobber each other's transcript on exit).
CUR="${CLAUDE_CODE_SESSION_ID:-}"

# Strip the child/SDK markers before anything else runs in the new window.
# We are launched from inside a claude session, so without this the window --
# and every tab or shell later opened from it -- inherits
# CLAUDE_CODE_CHILD_SESSION, and claude started there DOES NOT PERSIST ITS
# TRANSCRIPT. That is the env leak that silently destroyed sessions in June
# 2026 (data/DEBUG-data-loss.md); 2.1.277 at least warns about it now.
# spawn_fork.sh and browse_tui.py already strip these; this path did not.
CLEAN_ENV="env -u CLAUDE_CODE_ENTRYPOINT -u CLAUDE_CODE_CHILD_SESSION \
-u CLAUDE_CODE_SESSION_ID -u CLAUDECODE -u CLAUDE_CODE_EXECPATH \
-u CLAUDE_CODE_SESSION_ATTENDED -u CLAUDE_CODE_MESSAGING_SOCKET \
-u CLAUDE_CODE_MESSAGING_TOKEN -u CLAUDE_PID -u AI_AGENT"

inner="$CLEAN_ENV ASK_EXPERT_CURRENT_SESSION=$(printf '%q' "$CUR") python3 $TUI; echo; echo '(session ended — press enter to close)'; read"

# 1) tmux
if [ -n "${TMUX:-}" ] && command -v tmux >/dev/null 2>&1; then
  if tmux new-window -n "browse-experts" "$inner" 2>/dev/null; then
    echo "TUI_OPENED: new tmux window 'browse-experts'"
    exit 0
  fi
fi

# 2) GNU screen
if [ -n "${STY:-}" ] && command -v screen >/dev/null 2>&1; then
  if screen -X screen -t "browse-experts" bash -lc "$inner" 2>/dev/null; then
    echo "TUI_OPENED: new screen window 'browse-experts'"
    exit 0
  fi
fi

# 3) Graphical terminal (only if a display is available)
if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
  if command -v gnome-terminal >/dev/null 2>&1; then
    if gnome-terminal --geometry=140x40 -- bash -lc "$inner" 2>/dev/null; then
      echo "TUI_OPENED: new gnome-terminal window"
      exit 0
    fi
  fi
  if command -v xfce4-terminal >/dev/null 2>&1; then
    if xfce4-terminal --geometry=140x40 --command="bash -lc \"$inner\"" 2>/dev/null; then
      echo "TUI_OPENED: new xfce4-terminal window"
      exit 0
    fi
  fi
  if command -v alacritty >/dev/null 2>&1; then
    if alacritty -o "window.dimensions.columns=140" -o "window.dimensions.lines=40" \
                 -e bash -lc "$inner" >/dev/null 2>&1 &
    then
      echo "TUI_OPENED: new alacritty window"
      exit 0
    fi
  fi
  if command -v konsole >/dev/null 2>&1; then
    if konsole -e bash -lc "$inner" >/dev/null 2>&1 &
    then
      echo "TUI_OPENED: new konsole window"
      exit 0
    fi
  fi
  if command -v xterm >/dev/null 2>&1; then
    if xterm -geometry 140x40 -e bash -lc "$inner" >/dev/null 2>&1 &
    then
      echo "TUI_OPENED: new xterm window"
      exit 0
    fi
  fi
fi

# 4) Text fallback (SSH, headless, etc.)
echo "NO_DISPLAY: cannot open a new terminal window (no \$TMUX, no \$DISPLAY)."
echo "Falling back to inline list — pick a session by number in the chat."
echo "To run the curses TUI yourself: exit claude, then run:"
echo "  python3 $TUI"
echo
python3 "$LIST"
exit 2
