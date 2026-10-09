---
description: Summarize what was accomplished in this session and append it to the parent session's report file so future forks see the update.
argument-hint: [optional extra notes]
allowed-tools: Bash(python3:*), Read, Write
---

The user wants to /report-back to the parent session.

Step 1 — discover session lineage and report path. Parse the `key=value` lines:

!`python3 /home/bik/.claude/plugins/ask-expert/scripts/report_back_setup.py`

Step 2 — compose the report.

Write a concise summary of what was accomplished in THIS conversation so far. Cover:
- 2–6 short bullets describing what changed or was decided
- Files modified (if any)
- Open questions, blockers, or follow-ups (if any)

If the user supplied extra notes after `/report-back`, include them verbatim at the end of your summary:

User-supplied notes: $ARGUMENTS

Step 3 — append to the report file.

Format the new entry **exactly** like this (no leading whitespace before the heading):

```
## Report from <current_session_short> · <timestamp>

<your bulleted summary>

<extra user notes if any>

---
```

- If `report_exists=true`, first **Read** `report_file_path`, then **Write** back the existing content with your new entry appended at the end.
- If `report_exists=false`, **Write** a new file containing just your new entry, prefixed with a top-level heading: `# Reports for session <target_session_id>\n\n` followed by the entry.

Step 4 — confirm to the user in one line: "Report saved to <report_file_path>. Future forks of session <target_session_short> will see it."

Do NOT do anything else.
