#!/usr/bin/env bash
# Launch claude on the given session in a separate window.
#
# Usage: spawn_fork.sh <session-id> [fork|continue] [permissive]
#
# Resolution order matches spawn_browse.sh: tmux > screen > graphical terminal
# > inline instruction (SSH / no display).
set -u

session_id="${1:-}"
mode="${2:-fork}"
permissive="${3:-}"

if [ -z "$session_id" ]; then
  echo "usage: spawn_fork.sh <session-id> [fork|continue] [permissive]" >&2
  exit 2
fi

resolver=/home/bik/.claude/plugins/ask-expert/scripts/resolve_session_cwd.py
cwd="$(python3 "$resolver" "$session_id" 2>/dev/null || true)"

claude_args="--resume $(printf '%q' "$session_id")"
if [ "$mode" = "fork" ]; then
  claude_args+=" --fork-session"
fi
if [ "$permissive" = "permissive" ] || [ "$permissive" = "1" ]; then
  claude_args+=" --dangerously-skip-permissions"
fi

# Append sibling-fork reports via --append-system-prompt if any exist.
report_file=/home/bik/.claude/plugins/ask-expert/data/reports/$session_id.md
if [ -f "$report_file" ]; then
  preface="# Reports from sibling forks of this session"$'\n\n'
  preface+="The following are summaries written via /report-back by other "
  preface+="Claude sessions forked from this same parent. Treat them as "
  preface+="authoritative updates about work that has happened since this "
  preface+="session's transcript was recorded."$'\n\n'
  appended="$preface$(cat "$report_file")"
  claude_args+=" --append-system-prompt $(printf '%q' "$appended")"
fi

guard=/home/bik/.claude/plugins/ask-expert/scripts/session_guard.py

# Inner command run in the spawned window:
#   1. concurrent-open guard — refuse-by-default if this session id is already
#      open in another claude process (prevents transcript clobber).
#   2. snapshot the transcript before resuming (recoverable rollback safety).
#   3. launch claude.
# A fork makes a NEW session id, so the concurrent-open check only matters for
# continue/resume of an existing id.
guard_block=""
if [ "$mode" != "fork" ]; then
  guard_block="if python3 $guard check-open $(printf '%q' "$session_id") 2>/tmp/ask-expert-open.$$; then "
  guard_block+="echo; echo '⚠  session '$(printf '%q' "${session_id:0:8}")' is ALREADY OPEN in another claude window:'; "
  guard_block+="cat /tmp/ask-expert-open.$$; rm -f /tmp/ask-expert-open.$$; "
  guard_block+="echo 'Opening it here too can make the two windows overwrite each other'\\''s transcript'; "
  guard_block+="read -p 'open anyway? [y/N] ' _a; [ \"\$_a\" = y ] || [ \"\$_a\" = Y ] || exit 0; "
  guard_block+="fi; rm -f /tmp/ask-expert-open.$$; "
fi
snap_block="python3 $guard snapshot $(printf '%q' "$session_id") 2>/dev/null; "

# Hold a lock for the resumed id while claude runs (continue only — a fork
# mints a new id, so locking the parent would be wrong). Released on exit.
lock_pre="" ; lock_post=""
if [ "$mode" != "fork" ]; then
  lock_pre="python3 $guard lock $(printf '%q' "$session_id") 2>/dev/null; "
  lock_post="python3 $guard unlock $(printf '%q' "$session_id") 2>/dev/null; "
fi

# Reminder shown right before claude starts: Ctrl-C abort skips the transcript
# flush for resumed sessions, so work is lost. Clean exit (Ctrl-D / /exit) saves.
reminder="printf '\\033[1;33m%s\\033[0m\\n' '⚠  To SAVE your work, exit with Ctrl-D or /exit — NOT Ctrl-C Ctrl-C (that aborts before saving).'; "

cmd=""
if [ -n "$cwd" ] && [ -d "$cwd" ]; then
  cmd="cd $(printf '%q' "$cwd") && "
fi
# Launch claude with a CLEAN environment: this script runs as a child of a
# claude session, so CLAUDE_CODE_ENTRYPOINT=sdk-cli / CLAUDE_CODE_CHILD_SESSION=1
# are inherited and would mark the spawned claude as a non-persisting child/SDK
# session. `env -u …` strips them so it runs as a normal interactive session,
# and CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1 forces the transcript to persist.
clean="env -u CLAUDE_CODE_ENTRYPOINT -u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_CODE_SESSION_ID -u CLAUDECODE -u CLAUDE_CODE_EXECPATH -u AI_AGENT CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1"
cmd="${guard_block}${snap_block}${lock_pre}${reminder}${cmd}${clean} claude $claude_args; ${lock_post}echo; echo '(session ended — press enter to close)'; read"

# 1) tmux
if [ -n "${TMUX:-}" ] && command -v tmux >/dev/null 2>&1; then
  if tmux new-window -n "$mode-$session_id" "$cmd" 2>/dev/null; then
    echo "$mode session opened in new tmux window (cwd: ${cwd:-default})"
    exit 0
  fi
fi

# 2) GNU screen
if [ -n "${STY:-}" ] && command -v screen >/dev/null 2>&1; then
  if screen -X screen -t "$mode-${session_id:0:8}" bash -lc "$cmd" 2>/dev/null; then
    echo "$mode session opened in new screen window (cwd: ${cwd:-default})"
    exit 0
  fi
fi

# 3) Graphical terminal
if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
  if command -v gnome-terminal >/dev/null 2>&1; then
    if gnome-terminal -- bash -lc "$cmd" 2>/dev/null; then
      echo "$mode session opened in new gnome-terminal window (cwd: ${cwd:-default})"
      exit 0
    fi
  fi
  if command -v xfce4-terminal >/dev/null 2>&1; then
    if xfce4-terminal --command="bash -lc \"$cmd\"" 2>/dev/null; then
      echo "$mode session opened in new xfce4-terminal window"
      exit 0
    fi
  fi
  if command -v alacritty >/dev/null 2>&1; then
    if alacritty -e bash -lc "$cmd" >/dev/null 2>&1 &
    then
      echo "$mode session opened in new alacritty window"
      exit 0
    fi
  fi
  if command -v konsole >/dev/null 2>&1; then
    if konsole -e bash -lc "$cmd" >/dev/null 2>&1 &
    then
      echo "$mode session opened in new konsole window"
      exit 0
    fi
  fi
  if command -v xterm >/dev/null 2>&1; then
    if xterm -e bash -lc "$cmd" >/dev/null 2>&1 &
    then
      echo "$mode session opened in new xterm window"
      exit 0
    fi
  fi
fi

# 4) Text fallback: print the command for the user to run themselves
echo "NO_DISPLAY: cannot open a new terminal window (no \$TMUX, no \$DISPLAY)."
echo
echo "To $mode session $session_id, exit claude (Ctrl-D) and run:"
echo
if [ -n "$cwd" ] && [ -d "$cwd" ]; then
  echo "  cd $cwd && claude $claude_args"
else
  echo "  claude $claude_args"
fi
echo
exit 2
