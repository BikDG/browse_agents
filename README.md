# browse_agents

A Claude Code plugin for working with your **past sessions**: browse them as a
navigable tree, search them (substring, LLM-ranked, or local FAISS semantic
search), and **fork or continue** any of them into a new terminal/tmux window —
with guards that stop two claude processes from clobbering each other's
transcript.

Commands it adds: `/browse-experts`, `/ask-expert`, `/report-back`, `/save`.

---

## Quick start (plug and play)

```bash
# 1. clone
git clone https://github.com/BikDG/browse_agents
cd browse_agents

# 2. configure (interactive): data dir, terminal, semantic search, `cont` alias
bash scripts/install.sh

# 3. load the plugin into Claude Code — pick ONE:
#    (a) quickest, no install, works with a PRIVATE repo:
claude --plugin-dir "$PWD"
#    (b) permanent install (needs the plugin registered in Claude Code):
#        inside claude:  /plugin marketplace add BikDG/browse_agents
#                        /plugin install ask-expert@browse-agents

# 4. build the session index the first time (so the TUI has something to show):
python3 "$PWD/scripts/index_sessions.py"      # scans ~/.claude/projects, ~1 min+

# 5. (optional) if you enabled semantic search in step 2, embed your sessions:
DATA=$(grep '^ASK_EXPERT_DATA_DIR=' ~/.config/ask-expert/config.env | cut -d= -f2)
"$DATA/venv/bin/python" "$PWD/scripts/semantic.py" build
```

Then run `/browse-experts` inside Claude Code. That's it — from here the index
and embeddings stay current automatically on each launch.

> **`install.sh` only *configures* the plugin. It does NOT register it with
> Claude Code** — step 3 does. This trips everyone up once.

---

## The commands

- **`/browse-experts`** — curses TUI: a tree of your past sessions (forks nested
  under their parents), with folders, search, star/rename, and fork/continue into
  a new window. Over plain SSH with no display it falls back to an in-chat list.
- **`/ask-expert <question>`** — ranks past sessions by relevance to a question
  (FAISS recall → LLM rerank when semantic search is enabled) and offers to
  fork/continue the best match.
- **`/report-back [notes]`** — summarizes the current session into its parent's
  report file so future forks inherit the update.
- **`/save`** — checkpoints the current session's transcript and prints how to
  resume it (by session id).

### TUI keys

```
↑↓ / j k   move            enter   open (continue) the session
→ / space  expand          f       fork into a new window
← / h      collapse        s       star / unstar
/          substring search  r     rename session or folder
w          FAISS semantic search   d   make a folder    m   move into a folder
x          detach a node (becomes its own root)
v          saved-chats panel       F5  rescan (re-index)
del        trash (move JSONL to data/trash)   esc  clear search    q  quit
```

`/` is instant substring matching ("I know the word"); **`w`** is semantic
("I remember what it was about") and needs semantic search set up. They're
mutually exclusive; `esc` returns to the tree.

### The `cont` alias

`install.sh` can add `cont` = `claude --dangerously-skip-permissions -p
'/browse-experts'` — a one-word launcher for the picker from any shell. **It only
works once the plugin is permanently installed (step 3b), not with
`--plugin-dir`.**

---

## Configuration

`install.sh` writes `~/.config/ask-expert/config.env`. The plugin reads it; a real
environment variable always overrides the file.

| Setting | Meaning | Default |
|---|---|---|
| `ASK_EXPERT_DATA_DIR` | where runtime state lives | `$XDG_DATA_HOME/ask-expert` → `~/.local/share/ask-expert` |
| `ASK_EXPERT_TERMINAL` | terminal command for windowed spawns (expects a `-e CMD` convention) | auto-detect: tmux > screen > gnome-terminal > xfce4 > alacritty > konsole > xterm |
| `ASK_EXPERT_SEMANTIC` | `off` / `local` / `voyage` | `off` |

**Data lives OUTSIDE the plugin install dir** (the `data/` under your chosen
`ASK_EXPERT_DATA_DIR`), so a plugin reinstall/upgrade never wipes your index,
folders, stars, embeddings, or transcript backups. Don't point it back inside the
plugin dir.

---

## How indexing works (so nothing surprises you)

- The index is built from Claude Code's own transcripts at
  **`~/.claude/projects/*/*.jsonl`** (not configurable — that's where Claude Code
  stores them). If you've relocated those, the plugin won't find them.
- `/browse-experts` kicks off `index_sessions.py` in the **background** on every
  launch, then (if semantic is on) an incremental embed. The **very first** run
  classifies your whole history via Haiku and takes minutes and some Haiku tokens,
  so the first TUI open may show "No sessions indexed yet" — that's just timing.
  Build it once in the foreground (Quick start step 4) and re-open.
- Classification scans the **300 most-recently-modified** sessions per run;
  already-indexed sessions are retained indefinitely.

---

## Semantic search (optional, local & free by default)

Upgrades search from substring/LLM ranking to vector search (`/ask-expert` recall
and the TUI `w` key).

```bash
bash scripts/setup_local.sh         # venv + CPU torch + sentence-transformers + bge-small
DATA=$(grep '^ASK_EXPERT_DATA_DIR=' ~/.config/ask-expert/config.env | cut -d= -f2)
"$DATA/venv/bin/python" scripts/semantic.py build     # embed sessions (progress bar)
"$DATA/venv/bin/python" scripts/semantic.py stats     # check coverage
```

- **Local by default** — `sentence-transformers`, `BAAI/bge-small-en-v1.5`
  (384-dim). No API key, nothing leaves the machine. Runs on CPU.
- **Always run the embedder with the venv's python**
  (`"$DATA/venv/bin/python" …`), never system `python3` — the deps live in the venv.
- **Index first, then embed.** `semantic.py build` reads `index.json`; if the index
  is empty it embeds nothing. `stats` tells you the state:
  `indexed: 0` → build the index first; `indexed N, embedded 0` → run `build`;
  `indexed N, embedded N` → done.
- Switch to the **Voyage API** instead by editing
  `$ASK_EXPERT_DATA_DIR/semantic/config.json` to `{"provider":"voyage",...}` and
  running `scripts/setup_voyage.sh` (needs a key).
- After the first `build`, you never repeat it manually — `/browse-experts`
  re-embeds new sessions incrementally.

---

## Prerequisites

- **Claude Code**, recent (uses `--fork-session`, `--session-id`,
  `${CLAUDE_PLUGIN_ROOT}`, `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE`).
- `python3`, `tmux` (SSH/headless path), `jq`, and a graphical terminal for
  windowed spawns.
- `gh` only for GitHub-reading features.
- Semantic search additionally installs (into its own venv) `faiss-cpu`, CPU
  `torch`, `sentence-transformers` — a few hundred MB, one time.

---

## Troubleshooting (the exact things that bite)

**`/browse-experts isn't installed in this session`** (e.g. from the `cont` alias)
→ the plugin isn't loaded. You ran `install.sh` (which only configures) but didn't
do step 3. Use `claude --plugin-dir /path/to/browse_agents`, or `/plugin install`.

**`/plugin marketplace add` fails to clone** → the repo is private; Claude can't
fetch it. Either make it public, give Claude's `gh`/git access on that machine, or
just use `claude --plugin-dir /path/to/clone` (reads the local clone, no auth).

**`No sessions indexed yet`** in the TUI → the background indexer hadn't finished
(first run) or found nothing. Run `python3 scripts/index_sessions.py` in the
foreground; watch for `indexed N sessions`. `indexed 0` means little/no history
under `~/.claude/projects`, or `claude -p` can't run for classification
(PATH/auth) — the foreground output will say which.

**`No such file …/venv/bin/python`** when embedding → semantic search isn't set up.
Run `bash scripts/setup_local.sh`, then the `build` command.

**`w` says "semantic search not set up"** → same cause; set up + `build` once.

**Data ended up inside the plugin dir** → you pinned `ASK_EXPERT_DATA_DIR` there.
Prefer the default (`~/.local/share/ask-expert`) so reinstalls don't wipe it.

---

## Privacy

All runtime state is git-ignored and never ships; the default embedding model runs
offline; no personal content is in this repo.

## Uninstall / revert

Remove the plugin via `/plugin`, then delete `$ASK_EXPERT_DATA_DIR` and
`~/.config/ask-expert/`. To disable only semantic search, delete
`$ASK_EXPERT_DATA_DIR/{venv,semantic}`.
