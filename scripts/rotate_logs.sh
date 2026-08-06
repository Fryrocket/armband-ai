#!/usr/bin/env bash
# Manual / cron-friendly rotation + gzip for armband-ai text logs.
# Usage:
#   bash scripts/rotate_logs.sh
#   LOG_MAX_BYTES=5242880 LOG_KEEP=10 LOG_COMPRESS=1 bash scripts/rotate_logs.sh
#
# Active:  logs/foo.log
# Archives: logs/foo.log.1.gz … logs/foo.log.N.gz
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
MAX_BYTES="${LOG_MAX_BYTES:-2097152}"
KEEP="${LOG_KEEP:-5}"
COMPRESS="${LOG_COMPRESS:-1}"

compress_file() {
  local src="$1"
  [[ -f "$src" ]] || return 0
  [[ "$COMPRESS" == "1" ]] || return 0
  command -v gzip >/dev/null 2>&1 || return 0
  gzip -f -n "$src" 2>/dev/null || true
}

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
    if [[ -f "${f}.${i}.gz" ]]; then
      if (( i == KEEP )); then
        rm -f "${f}.${i}.gz" || true
      else
        mv -f "${f}.${i}.gz" "${f}.$((i + 1)).gz" || true
      fi
    fi
    if [[ -f "${f}.${i}" ]]; then
      if (( i == KEEP )); then
        rm -f "${f}.${i}" || true
      else
        mv -f "${f}.${i}" "${f}.$((i + 1))" || true
      fi
    fi
  done

  mv -f "$f" "${f}.1"
  compress_file "${f}.1"
  : >"$f"

  if [[ -f "${f}.1.gz" ]]; then
    echo "rotated+gzip $f -> ${f}.1.gz (was $size bytes)"
  else
    echo "rotated $f -> ${f}.1 (was $size bytes)"
  fi
}

mkdir -p "$LOG_DIR"
echo "LOG_DIR=$LOG_DIR MAX_BYTES=$MAX_BYTES KEEP=$KEEP COMPRESS=$COMPRESS"

shopt -s nullglob
for f in "$LOG_DIR"/*.log; do
  [[ "$f" =~ \.log\.[0-9]+(\.gz)?$ ]] && continue
  rotate_one "$f"
done

echo "done"
