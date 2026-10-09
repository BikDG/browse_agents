---
description: Save the current chat and print its `claude --resume` command, without exiting.
allowed-tools: Bash(python3:*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/save_session.py:*)
---

The user wants to save the current session and see how to resume it, the same
line `/exit` prints, but without exiting.

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/save_session.py`

Relay the output above to the user verbatim, in a code block. Do not paraphrase
it, do not re-run it, and do not add commentary beyond one short sentence.

- If the first line starts with `SAVE_FAILED:`, show that line and stop.
- If the output includes the `NOTE:` about unflushed turns, keep it. It is the
  difference between what was saved and what is in the session right now.
- The session continues normally afterwards. Do not exit, do not compact, and
  do not suggest the user do either unless the NOTE appeared.
