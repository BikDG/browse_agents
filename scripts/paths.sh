# Shared path resolution for ask-expert shell scripts. Source this:
#     source "$(dirname "$0")/paths.sh"
# Exposes:
#   PLUGIN_ROOT - plugin install root
#   DATA_DIR    - per-user runtime state (XDG), kept outside the install tree
# Precedence for overrides: real env var > ~/.config/ask-expert/config.env.

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load installer config for any ASK_EXPERT_* / XDG vars not already in the env.
_ae_cfg="${XDG_CONFIG_HOME:-$HOME/.config}/ask-expert/config.env"
if [ -f "$_ae_cfg" ]; then
  while IFS='=' read -r _k _v; do
    case "$_k" in ''|\#*) continue ;; esac
    _v="${_v%\"}"; _v="${_v#\"}"; _v="${_v%\'}"; _v="${_v#\'}"
    eval "_cur=\${$_k:-}"
    [ -z "$_cur" ] && export "$_k=$_v"
  done < "$_ae_cfg"
fi
unset _ae_cfg _k _v _cur

if [ -n "${ASK_EXPERT_DATA_DIR:-}" ]; then
  DATA_DIR="$ASK_EXPERT_DATA_DIR"
elif [ -n "${XDG_DATA_HOME:-}" ]; then
  DATA_DIR="$XDG_DATA_HOME/ask-expert"
else
  DATA_DIR="$HOME/.local/share/ask-expert"
fi
mkdir -p "$DATA_DIR" 2>/dev/null || true
