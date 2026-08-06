#!/usr/bin/env bash
# Rotate logs/*.log when over size.
# Default compression: zstd. LOG_COMPRESSION=zstd|gzip|none
# Env: LOG_DIR LOG_MAX_BYTES LOG_KEEP LOG_COMPRESSION
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
MAX_BYTES="${LOG_MAX_BYTES:-2097152}"
KEEP="${LOG_KEEP:-5}"
if [[ -n "${LOG_COMPRESSION:-}" ]]; then
  COMPRESSION="${LOG_COMPRESSION}"
elif [[ "${LOG_COMPRESS:-1}" == "0" ]]; then
  COMPRESSION="none"
else
  COMPRESSION="zstd"
fi
COMPRESSION="$(echo "$COMPRESSION" | tr '[:upper:]' '[:lower:]')"
case "$COMPRESSION" in
  gzip|gz) COMPRESSION="gzip" ;;
  none|off|false|0) COMPRESSION="none" ;;
  *) COMPRESSION="zstd" ;;
esac

compress_file() {
  local src="$1"
  [[ -f "$src" ]] || return 0
  case "$COMPRESSION" in
    gzip)
      command -v gzip >/dev/null 2>&1 || return 0
      gzip -f -n "$src" 2>/dev/null || true
      ;;
    zstd)
      command -v zstd >/dev/null 2>&1 || return 0
      zstd -f -q --rm "$src" 2>/dev/null || true
      ;;
  esac
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
    for ext in ".zst" ".gz" ""; do
      local p="${f}.${i}${ext}"
      if [[ -f "$p" ]]; then
        if (( i == KEEP )); then
          rm -f "$p" || true
        else
          mv -f "$p" "${f}.$((i + 1))${ext}" || true
        fi
      fi
    done
  done

  mv -f "$f" "${f}.1"
  compress_file "${f}.1"
  : >"$f"
  echo "rotated $f ($size bytes)"
}

mkdir -p "$LOG_DIR"
echo "LOG_DIR=$LOG_DIR MAX_BYTES=$MAX_BYTES KEEP=$KEEP COMPRESSION=$COMPRESSION"

shopt -s nullglob
for f in "$LOG_DIR"/*.log; do
  [[ "$f" =~ \.log\.[0-9]+(\.(gz|zst))?$ ]] && continue
  rotate_one "$f"
done
echo "done"
