#!/usr/bin/env bash
# Configure local git so pull always rebases, and install a simple post-checkout
# reminder hook. Optional: enable systemd timer for periodic auto-pull.
#
# Usage (from repo root on the Pi):
#   bash scripts/install_git_hooks.sh
#   bash scripts/install_git_hooks.sh --timer   # also enable hourly auto-pull
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "Repo: $ROOT"

# Always rebase on git pull
git config pull.rebase true
git config rebase.autoStash false
echo "Set: pull.rebase=true"

# Local hooks path (does not affect other repos)
HOOKS_DIR="$ROOT/.githooks"
mkdir -p "$HOOKS_DIR"

cat > "$HOOKS_DIR/post-checkout" << 'EOF'
#!/usr/bin/env bash
# Runs after checkout/branch switch — reminder only (no network pull here).
if [[ "${3:-}" == "1" ]]; then
  echo "[armband-ai] Tip: git pull --rebase   or   bash scripts/git_auto_pull.sh"
fi
EOF
chmod +x "$HOOKS_DIR/post-checkout"

# Optional: run auto-pull after a successful merge (e.g. after manual pull)
cat > "$HOOKS_DIR/post-merge" << 'EOF'
#!/usr/bin/env bash
echo "[armband-ai] merge complete → $(git rev-parse --short HEAD)"
EOF
chmod +x "$HOOKS_DIR/post-merge"

git config core.hooksPath .githooks
echo "Set: core.hooksPath=.githooks"

if [[ "${1:-}" == "--timer" ]]; then
  UNIT_DIR="${HOME}/.config/systemd/user"
  mkdir -p "$UNIT_DIR"

  cat > "$UNIT_DIR/armband-git-pull.service" << EOF
[Unit]
Description=Armband AI git auto-pull (rebase)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$ROOT
ExecStart=/bin/bash $ROOT/scripts/git_auto_pull.sh
EOF

  cat > "$UNIT_DIR/armband-git-pull.timer" << 'EOF'
[Unit]
Description=Hourly armband-ai git auto-pull

[Timer]
OnBootSec=5min
OnUnitActiveSec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF

  systemctl --user daemon-reload
  systemctl --user enable --now armband-git-pull.timer
  echo "Enabled user timer: armband-git-pull.timer (hourly)"
  systemctl --user list-timers armband-git-pull.timer || true
else
  echo
  echo "Hooks installed. Optional hourly auto-pull:"
  echo "  bash scripts/install_git_hooks.sh --timer"
fi

echo
echo "Manual pull:"
echo "  bash scripts/git_auto_pull.sh"
echo "  # or: git pull --rebase"
