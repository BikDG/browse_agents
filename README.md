# browse_agents

A Claude Code plugin for working with your **past sessions**: browse them as a
navigable tree, search them (keyword or optional local semantic search), and
**fork or continue** any of them into a new window — with guards that stop two
claude processes from clobbering each other's transcript.

> ⚠️ **Status: SCAFFOLD — not yet portable.**
> This repo currently mirrors the plugin as it ran on one machine. Scripts and
> command files still contain **hardcoded `/home/bik/...` paths** and Linux/GNOME
> assumptions. It will NOT work unmodified on another machine yet. De-hardcoding
> (switch to `${CLAUDE_PLUGIN_ROOT}` / `Path(__file__)`, move user data to an XDG
> dir) is the next pass. Until then, treat this as source/reference.

## Commands

- `/browse-experts` — curses TUI: tree of past sessions (forks nested under
  parents), folders, search, star/rename, and fork/continue into a new terminal
  or tmux window. Falls back to an in-chat list over plain SSH.
- `/ask-expert <question>` — rank past sessions by relevance to a question and
  offer to fork/continue the best match (optionally semantic-recall-backed).
- `/report-back [notes]` — summarize the current session into its parent's report
  file so future forks inherit the update.
- `/save` — checkpoint the current session's transcript and print how to resume it.

## Prerequisites

- Claude Code (recent version — relies on `--fork-session`, `--session-id`,
  `${CLAUDE_PLUGIN_ROOT}`, `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE`).
- `python3`, `tmux` (for the SSH/headless path), `jq`, and a graphical terminal
  (`gnome-terminal`/`xfce4`/`alacritty`/`konsole`/`xterm`) for windowed spawns.
- `gh` (authenticated) — only for features that read GitHub.

## Optional: local semantic search (free, offline)

Session search can be upgraded from keyword/LLM ranking to vector search:

```bash
bash scripts/setup_local.sh        # venv + CPU torch + sentence-transformers + bge-small
<plugin>/data/venv/bin/python scripts/semantic.py build   # embed sessions
```

- **Local by default** (`sentence-transformers`, `BAAI/bge-small-en-v1.5`, 384-dim)
  — no API key, nothing leaves the machine.
- Pluggable: edit `data/semantic/config.json` to `{"provider":"voyage",...}` and
  run `scripts/setup_voyage.sh` if you'd rather use the Voyage API.
- **Entirely optional.** Without setup, `/ask-expert` uses the built-in LLM ranking;
  the semantic feature no-ops.

## Install (once de-hardcoded)

As a marketplace plugin:

```
/plugin marketplace add BikDG/browse_agents
/plugin install ask-expert@browse-agents
```

Or for development: `claude --plugin-dir /path/to/browse_agents`.

## Data & privacy

All runtime state (session index, folders, stars, titles, embeddings, transcript
backups) lives under the plugin's `data/` dir and is **git-ignored** — it never
ships. The local embedding model runs offline. No personal content is included in
this repo.

## Uninstall / revert

Remove the plugin via `/plugin`, and delete its `data/` dir. The semantic feature
alone can be disabled by deleting `data/venv` and `data/semantic`.
