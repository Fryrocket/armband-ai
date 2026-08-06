#!/usr/bin/env bash
# Manual / cron-friendly rotation for armband-ai text logs.
# Usage:
#   bash scripts/rotate_logs.sh
#   LOG_MAX_BYTES=5242880 LOG_KEEP=10 bash scripts/rotate_logs.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
MAX_BYTES="${LOG_MAX_BYTES:-2097152}"
KEEP="${LOG_KEEP:-5}"

rotate_one() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  local size
  size="$(wc -c <"$f" 2>/dev/null | tr -d ' ' || echo 0)"
  [[ "$size" =~ ^[0-9]+$ ]] || size=0
  if (( size < MAX_BYTES )); then
    echo "ok  $f ($size bytes)"
    return 0
  fi
  local i
  for (( i=KEEP; i>=1; i-- )); do
    if [[ -f "${f}.${i}" ]]; then
      if (( i == KEEP )); then
        rm -f "${f}.${i}" || true
      else
        mv -f "${f}.${i}" "${f}.$((i + 1))" || true
      fi
    fi
  done
  mv -f "$f" "${f}.1"
  : >"$f"
  echo "rotated $f -> ${f}.1 (was $size bytes)"
}

mkdir -p "$LOG_DIR"
echo "LOG_DIR=$LOG_DIR MAX_BYTES=$MAX_BYTES KEEP=$KEEP"

shopt -s nullglob
for f in "$LOG_DIR"/*.log; do
  # skip already-rotated numbered files
  [[ "$f" =~ \.log\.[0-9]+$ ]] && continue
  rotate_one "$f"
done

echo "done"
