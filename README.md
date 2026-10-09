# browse_agents

A Claude Code plugin for working with your **past sessions**: browse them as a
navigable tree, search them (keyword, LLM-ranked, or optional local semantic
search), and **fork or continue** any of them into a new terminal/tmux window —
with guards that stop two claude processes from clobbering each other's
transcript.

> **Status:** de-hardcoded and portable (no machine-specific paths). Validated on
> the author's setup; **first cross-machine test pending**, so treat early installs
> as beta and report breakage.

## Commands

- `/browse-experts` — curses TUI: tree of past sessions (forks nested under
  parents), folders, search, star/rename, fork/continue into a new window.
  Falls back to an in-chat list over plain SSH.
- `/ask-expert <question>` — rank past sessions by relevance and offer to
  fork/continue the best match (semantic-recall-backed when enabled).
- `/report-back [notes]` — summarize the current session into its parent's report
  so future forks inherit the update.
- `/save` — checkpoint the current session's transcript and print how to resume it.

## Install

1. Clone the repo and run the interactive setup:
   ```bash
   git clone https://github.com/BikDG/browse_agents
   bash browse_agents/scripts/install.sh
   ```
   It detects your Claude Code install + any prior data dirs and lets you choose:
   the **data dir**, the **terminal** to spawn, whether to enable **semantic search**
   (local FAISS or Voyage), and whether to add the **`cont`** alias
   (`cont` = quick-launch `/browse-experts`). It writes
   `~/.config/ask-expert/config.env`.

2. Register the plugin inside Claude Code:
   ```
   /plugin marketplace add BikDG/browse_agents
   /plugin install ask-expert@browse-agents
   ```
   Or for development: `claude --plugin-dir /path/to/browse_agents`.

## Configuration

The plugin reads `~/.config/ask-expert/config.env` (written by `install.sh`); a
real environment variable always overrides the file.

| Setting | Meaning | Default |
|---|---|---|
| `ASK_EXPERT_DATA_DIR` | where runtime state lives | `$XDG_DATA_HOME/ask-expert` → `~/.local/share/ask-expert` |
| `ASK_EXPERT_TERMINAL` | terminal command for windowed spawns (expects `-e CMD`) | auto-detect (tmux > gnome > xfce4 > alacritty > …) |
| `ASK_EXPERT_SEMANTIC` | `off` / `local` / `voyage` | `off` |

Runtime data lives **outside** the install tree, so a plugin reinstall/upgrade
won't wipe your index, folders, stars, embeddings, or transcript backups.

## Optional: semantic search

Free and offline by default (no key, nothing leaves the machine):
```bash
bash scripts/setup_local.sh         # venv + CPU torch + sentence-transformers + bge-small
"$ASK_EXPERT_DATA_DIR/venv/bin/python" scripts/semantic.py build   # embed sessions
```
Pluggable: edit `$ASK_EXPERT_DATA_DIR/semantic/config.json` to
`{"provider":"voyage",...}` and run `scripts/setup_voyage.sh` to use the Voyage API
instead. Without setup, `/ask-expert` uses keyword/LLM ranking and the feature
no-ops.

## Prerequisites

- Claude Code (recent — uses `--fork-session`, `--session-id`,
  `${CLAUDE_PLUGIN_ROOT}`, `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE`).
- `python3`, `tmux` (SSH/headless path), `jq`, a graphical terminal for windowed
  spawns. `gh` only for GitHub-reading features.

## Privacy

All runtime state is git-ignored and never ships; the local embedding model runs
offline; no personal content is in this repo.

## Uninstall / revert

Remove the plugin via `/plugin`, delete `$ASK_EXPERT_DATA_DIR` and
`~/.config/ask-expert/`. Disable only semantic search by deleting
`$ASK_EXPERT_DATA_DIR/{venv,semantic}`.
