#!/usr/bin/env bash
# Wrapper script for systemd --user: discovers the live Hyprland session environment
# so hyprctl / grim / wpctl work from the service context.

set -euo pipefail

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# Discover the newest Hyprland instance.
HYPR_DIR="$XDG_RUNTIME_DIR/hypr"
if [ -d "$HYPR_DIR" ]; then
    SIG=$(ls -t "$HYPR_DIR" 2>/dev/null | head -1)
    if [ -n "$SIG" ] && [ -e "$HYPR_DIR/$SIG/.socket.sock" ]; then
        export HYPRLAND_INSTANCE_SIGNATURE="$SIG"
    fi
fi

# Discover the Wayland display.
for sock in "$XDG_RUNTIME_DIR"/wayland-*; do
    [ -e "$sock" ] && [[ "$sock" != *.lock ]] && { export WAYLAND_DISPLAY="$(basename "$sock")"; break; }
done

# Load .env next to this script's parent (the repo root).
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$REPO_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$REPO_DIR/.env"
    set +a
fi

exec uv run --project "$REPO_DIR" python -m server
