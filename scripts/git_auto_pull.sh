#!/usr/bin/env bash
# Safe auto-pull for armband-ai on the Pi.
# - Uses rebase
# - Skips if the working tree is dirty
# - Logs to logs/git_auto_pull.log
#
# Exit codes:
#   0  success or intentional skip (dirty tree, already up to date, no remote branch)
#   1  local/repo error (not a git repo, detached HEAD, bad state)
#   2  network / remote error (fetch failed, no origin)
#   3  rebase conflict or pull failure (rebase aborted if possible)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

mkdir -p "$ROOT/logs" || {
  echo "ERROR: cannot create logs/ under $ROOT" >&2
  exit 1
}
LOG="$ROOT/logs/git_auto_pull.log"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

log() {
  local line
  line="$(ts) $*"
  echo "$line" | tee -a "$LOG"
}

fail() {
  local code="$1"
  shift
  log "ERROR: $*"
  exit "$code"
}

# On unexpected errors under set -e, log and exit 1
trap 'rc=$?; if [[ $rc -ne 0 ]]; then log "ERROR: unexpected failure (exit $rc) at line $LINENO"; fi' ERR

log "auto-pull start"

if ! command -v git >/dev/null 2>&1; then
  fail 1 "git not found on PATH"
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  fail 1 "not a git repo: $ROOT"
fi

# Detached HEAD is unsafe for pull --rebase of a branch
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
if [[ -z "$BRANCH" || "$BRANCH" == "HEAD" ]]; then
  fail 1 "detached HEAD – checkout a branch first (e.g. git checkout main)"
fi
log "branch=$BRANCH"

# Dirty tree → do not touch (not an error)
if [[ -n "$(git status --porcelain 2>/dev/null || true)" ]]; then
  log "dirty working tree – skip pull"
  git status --short 2>/dev/null | tee -a "$LOG" || true
  exit 0
fi

# origin required
if ! git remote get-url origin >/dev/null 2>&1; then
  fail 2 "remote 'origin' not configured"
fi

# Fetch with explicit failure
if ! git fetch origin 2> >(tee -a "$LOG" >&2); then
  fail 2 "git fetch origin failed (network or auth?)"
fi

LOCAL="$(git rev-parse HEAD)"
if ! REMOTE="$(git rev-parse "origin/${BRANCH}" 2>/dev/null)"; then
  log "no origin/$BRANCH – skip"
  exit 0
fi

if [[ "$LOCAL" == "$REMOTE" ]]; then
  log "already up to date ($(git rev-parse --short HEAD))"
  exit 0
fi

log "local=$(git rev-parse --short "$LOCAL") remote=$(git rev-parse --short "$REMOTE") – pull --rebase"

# Capture pull output; on conflict try to abort rebase
set +e
pull_out="$(git pull --rebase origin "$BRANCH" 2>&1)"
pull_rc=$?
set -e
echo "$pull_out" | tee -a "$LOG"

if [[ $pull_rc -ne 0 ]]; then
  if git rev-parse -q --verify REBASE_HEAD >/dev/null 2>&1 \
     || [[ -d "$(git rev-parse --git-path rebase-merge 2>/dev/null)" ]] \
     || [[ -d "$(git rev-parse --git-path rebase-apply 2>/dev/null)" ]]; then
    log "rebase conflict – aborting rebase to leave repo clean"
    if git rebase --abort 2>>"$LOG"; then
      log "rebase aborted successfully"
    else
      log "WARNING: git rebase --abort failed – check repo state manually"
    fi
  fi
  fail 3 "git pull --rebase failed (exit $pull_rc). Resolve conflicts manually if needed."
fi

log "ok now at $(git rev-parse --short HEAD)"
exit 0
