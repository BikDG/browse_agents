---
description: Find past Claude Code sessions that tackled a similar problem and offer to fork or continue one.
argument-hint: <topic or question>
allowed-tools: Bash(python3:*), Bash(bash:*), Bash(/home/bik/.claude/plugins/ask-expert/scripts/spawn_fork.sh:*)
---

The user wants expert help on: **$ARGUMENTS**

Step 1 — refresh the session index (lazy):

!`python3 /home/bik/.claude/plugins/ask-expert/scripts/index_sessions.py 2>&1`

Step 2 — find up to 10 ranked matches:

!`python3 /home/bik/.claude/plugins/ask-expert/scripts/find_match.py "$ARGUMENTS" 2>&1`

Now do the following, in order:

1. If the output of step 2 is `NO_MATCHES` (or empty), tell the user "no past session matches that topic well — proceeding fresh" and STOP. Do NOT invent matches.

2. Otherwise, present every match block from step 2 as a numbered list (1, 2, 3, …, up to 10). For each entry render exactly one line:

   ```
   <n>. [<score>] <title>  —  <one-line why>   (cwd: <cwd>)
   ```

   Keep it terse — no extra prose. After the list ask:

   > **Pick a session (1–N), or `n` to start fresh.**

3. When the user replies with a number, hold onto the corresponding `session_id` and ask the second prompt:

   > **`1` to fork (preserve the original) or `2` to continue (resume in place)?**

4. When the user answers `1` or `2`, run the spawn script. Use exact session_id and the mode keyword:

   - For fork: `bash /home/bik/.claude/plugins/ask-expert/scripts/spawn_fork.sh <session_id> fork`
   - For continue: `bash /home/bik/.claude/plugins/ask-expert/scripts/spawn_fork.sh <session_id> continue`

5. After the spawn returns, tell the user "new terminal opened — switch to it" and stop.

Do NOT try to answer the user's original question yourself — your only job is to route them to a past session.
