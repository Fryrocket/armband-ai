#!/usr/bin/env bash
# Configure local git so pull always rebases, and install local hooks.
# Optional: enable systemd user timer for periodic auto-pull.
#
# Usage (from repo root on the Pi):
#   bash scripts/install_git_hooks.sh
#   bash scripts/install_git_hooks.sh --timer
#
# Exit codes: 0 ok, 1 local error, 2 timer/systemd error
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || {
  echo "ERROR: cannot cd to $ROOT" >&2
  exit 1
}

echo "Repo: $ROOT"

if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git not found on PATH" >&2
  exit 1
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: not a git repository: $ROOT" >&2
  exit 1
fi

if ! git config pull.rebase true; then
  echo "ERROR: failed to set pull.rebase" >&2
  exit 1
fi
git config rebase.autoStash false || true
echo "Set: pull.rebase=true"

HOOKS_DIR="$ROOT/.githooks"
if ! mkdir -p "$HOOKS_DIR"; then
  echo "ERROR: cannot create $HOOKS_DIR" >&2
  exit 1
fi

cat > "$HOOKS_DIR/post-checkout" << 'EOF'
#!/usr/bin/env bash
# Runs after checkout/branch switch — reminder only (no network pull here).
if [[ "${3:-}" == "1" ]]; then
  echo "[armband-ai] Tip: git pull --rebase   or   bash scripts/git_auto_pull.sh"
fi
EOF

cat > "$HOOKS_DIR/post-merge" << 'EOF'
#!/usr/bin/env bash
echo "[armband-ai] merge complete → $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
EOF

chmod +x "$HOOKS_DIR/post-checkout" "$HOOKS_DIR/post-merge" || {
  echo "ERROR: chmod hooks failed" >&2
  exit 1
}

if ! git config core.hooksPath .githooks; then
  echo "ERROR: failed to set core.hooksPath" >&2
  exit 1
fi
echo "Set: core.hooksPath=.githooks"

# Ensure auto-pull script is executable
if [[ -f "$ROOT/scripts/git_auto_pull.sh" ]]; then
  chmod +x "$ROOT/scripts/git_auto_pull.sh" || true
else
  echo "WARNING: scripts/git_auto_pull.sh missing – pull after git pull --rebase"
fi

if [[ "${1:-}" == "--timer" ]]; then
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "ERROR: systemctl not available – cannot install timer" >&2
    exit 2
  fi

  UNIT_DIR="${HOME}/.config/systemd/user"
  if ! mkdir -p "$UNIT_DIR"; then
    echo "ERROR: cannot create $UNIT_DIR" >&2
    exit 2
  fi

  cat > "$UNIT_DIR/armband-git-pull.service" << EOF
[Unit]
Description=Armband AI git auto-pull (rebase)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$ROOT
ExecStart=/bin/bash $ROOT/scripts/git_auto_pull.sh
# Propagate non-zero exit from script for journal visibility
SuccessExitStatus=0
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

  if ! systemctl --user daemon-reload; then
    echo "ERROR: systemctl --user daemon-reload failed" >&2
    echo "Hint: if lingering is off, try: sudo loginctl enable-linger $USER" >&2
    exit 2
  fi

  if ! systemctl --user enable --now armband-git-pull.timer; then
    echo "ERROR: failed to enable armband-git-pull.timer" >&2
    exit 2
  fi

  echo "Enabled user timer: armband-git-pull.timer (hourly)"
  systemctl --user list-timers armband-git-pull.timer 2>/dev/null || true
  echo "Logs: journalctl --user -u armband-git-pull.service -n 50"
  echo "      also $ROOT/logs/git_auto_pull.log"
else
  echo
  echo "Hooks installed. Optional hourly auto-pull:"
  echo "  bash scripts/install_git_hooks.sh --timer"
fi

echo
echo "Manual pull:"
echo "  bash scripts/git_auto_pull.sh"
echo "  # or: git pull --rebase"
echo
echo "Exit codes for git_auto_pull.sh: 0=ok/skip 1=local 2=network 3=rebase conflict"
