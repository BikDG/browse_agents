"""Shared path resolution for ask-expert scripts (portable).

PLUGIN_DIR   - the plugin install root (this file's parent's parent).
DATA_DIR     - per-user runtime state, kept OUTSIDE the install tree so a plugin
               reinstall/upgrade can't wipe it. Resolution order:
                 $ASK_EXPERT_DATA_DIR
                 -> $XDG_DATA_HOME/ask-expert
                 -> ~/.local/share/ask-expert
PROJECTS_DIR - Claude Code's transcript dir (~/.claude/projects); not ours.
"""
import os
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def _config_file():
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ask-expert" / "config.env"


def _load_config_env():
    """Load the installer-written config (ASK_EXPERT_* etc). An actual env var
    always wins over the file (setdefault)."""
    try:
        for line in _config_file().read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


_load_config_env()


def _data_dir():
    env = os.environ.get("ASK_EXPERT_DATA_DIR")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "ask-expert"


DATA_DIR = _data_dir()
PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Ensure the data dir exists so callers don't each have to.
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass
