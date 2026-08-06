#!/usr/bin/env bash
# Safe auto-pull for armband-ai on the Pi.
# Exit: 0 ok/skip · 1 local · 2 network · 3 rebase conflict
# Env: GIT_PULL_LOG_MAX_BYTES (1MiB) GIT_PULL_LOG_KEEP (5) GIT_PULL_LOG_COMPRESS (1)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

mkdir -p "$ROOT/logs" || {
  echo "ERROR: cannot create logs/ under $ROOT" >&2
  exit 1
}
LOG="$ROOT/logs/git_auto_pull.log"
LOG_MAX_BYTES="${GIT_PULL_LOG_MAX_BYTES:-1048576}"
LOG_KEEP="${GIT_PULL_LOG_KEEP:-5}"
LOG_COMPRESS="${GIT_PULL_LOG_COMPRESS:-1}"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

compress_file() {
  local src="$1"
  [[ -f "$src" && "$LOG_COMPRESS" == "1" ]] || return 0
  command -v gzip >/dev/null 2>&1 || return 0
  gzip -f -n "$src" 2>/dev/null || true
}

rotate_log_if_needed() {
  local f="$1" max_bytes="$2" keep="$3"
  [[ -f "$f" ]] || return 0
  local size
  size="$(wc -c <"$f" 2>/dev/null | tr -d ' ' || echo 0)"
  [[ "$size" =~ ^[0-9]+$ ]] || size=0
  (( size < max_bytes )) && return 0

  local i
  for (( i=keep; i>=1; i-- )); do
    for ext in ".gz" ""; do
      local p="${f}.${i}${ext}"
      if [[ -f "$p" ]]; then
        if (( i == keep )); then
          rm -f "$p" 2>/dev/null || true
        else
          mv -f "$p" "${f}.$((i + 1))${ext}" 2>/dev/null || true
        fi
      fi
    done
  done
  mv -f "$f" "${f}.1" 2>/dev/null || true
  compress_file "${f}.1"
}

rotate_log_if_needed "$LOG" "$LOG_MAX_BYTES" "$LOG_KEEP"

log() {
  echo "$(ts) $*" | tee -a "$LOG"
}

fail() {
  local code="$1"
  shift
  log "ERROR: $*"
  exit "$code"
}

trap 'rc=$?; if [[ $rc -ne 0 ]]; then log "ERROR: unexpected failure (exit $rc) at line $LINENO"; fi' ERR

log "auto-pull start"

if ! command -v git >/dev/null 2>&1; then
  fail 1 "git not found on PATH"
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  fail 1 "not a git repo: $ROOT"
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
if [[ -z "$BRANCH" || "$BRANCH" == "HEAD" ]]; then
  fail 1 "detached HEAD – checkout a branch first (e.g. git checkout main)"
fi
log "branch=$BRANCH"

if [[ -n "$(git status --porcelain 2>/dev/null || true)" ]]; then
  log "dirty working tree – skip pull"
  git status --short 2>/dev/null | tee -a "$LOG" || true
  exit 0
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  fail 2 "remote 'origin' not configured"
fi

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
