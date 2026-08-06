#!/usr/bin/env bash
# Safe auto-pull for armband-ai on the Pi.
# - Uses rebase
# - Skips if the working tree is dirty
# - Logs to logs/git_auto_pull.log (under repo root)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p "$ROOT/logs"
LOG="$ROOT/logs/git_auto_pull.log"
ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

{
  echo "$(ts) auto-pull start"

  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "$(ts) not a git repo: $ROOT"
    exit 1
  fi

  # Dirty tree → do not touch
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "$(ts) dirty working tree – skip pull"
    git status --short
    exit 0
  fi

  BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  echo "$(ts) branch=$BRANCH"

  git fetch origin
  LOCAL="$(git rev-parse HEAD)"
  REMOTE="$(git rev-parse "origin/${BRANCH}" 2>/dev/null || true)"

  if [[ -z "$REMOTE" ]]; then
    echo "$(ts) no origin/$BRANCH – skip"
    exit 0
  fi

  if [[ "$LOCAL" == "$REMOTE" ]]; then
    echo "$(ts) already up to date"
    exit 0
  fi

  echo "$(ts) pulling with rebase..."
  git pull --rebase origin "$BRANCH"
  echo "$(ts) now at $(git rev-parse --short HEAD)"
} | tee -a "$LOG"
