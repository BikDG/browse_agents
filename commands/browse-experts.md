---
description: Browse past Claude Code sessions. Opens a curses TUI in a new window when possible; falls back to inline list selection over plain SSH.
allowed-tools: Bash(bash:*), Bash(/home/bik/.claude/plugins/ask-expert/scripts/spawn_browse.sh:*), Bash(/home/bik/.claude/plugins/ask-expert/scripts/spawn_fork.sh:*)
---

The user wants to browse past sessions and pick one to continue or fork.

Step 1 — attempt to launch the TUI:

!`bash /home/bik/.claude/plugins/ask-expert/scripts/spawn_browse.sh`

Now interpret the output:

- **If the first line starts with `TUI_OPENED:`** — the TUI is running in a new window (graphical terminal, tmux, or screen). In one short sentence, tell the user where it opened (e.g. "TUI opened in a new tmux window — switch to it"). STOP.

- **If the first line starts with `NO_DISPLAY:`** — we're in headless/SSH mode and the script printed a markdown table of sessions. Do this:
  1. Show the table to the user verbatim.
  2. Ask: **"Pick a session by number to continue (e.g. `3`) or fork (e.g. `3 f`). Add `p` for permissive mode (e.g. `3 f p`)."**
  3. When the user replies (e.g. `3`, `3 f`, or `3 f p`), look up the matching `session_id` from the table. Call:
     `bash /home/bik/.claude/plugins/ask-expert/scripts/spawn_fork.sh <session_id> <fork|continue> [permissive]`
  4. The spawn script will either open the session in a new tmux/graphical window (then you confirm), or print "NO_DISPLAY" with a `claude --resume …` command for the user to run themselves after exiting claude.
  5. Pass that command through to the user; do not pretend it ran.

- **If the output looks neither like `TUI_OPENED:` nor `NO_DISPLAY:`** — something else went wrong. Show the output and stop.

Do NOT try to answer any product question, list sessions yourself, or invent matches. Your only job is to launch the TUI or shepherd the text fallback.
