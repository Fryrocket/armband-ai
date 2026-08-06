#!/usr/bin/env bash
# Launch the live dashboard (accessible from phone on the same network)
# Exit: 0 ok · 1 missing deps / paths · other = streamlit exit code
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || {
  echo "ERROR: cannot cd to $ROOT" >&2
  exit 1
}

if [[ ! -f dashboard/app.py ]]; then
  echo "ERROR: dashboard/app.py not found under $ROOT" >&2
  exit 1
fi

if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

if ! command -v streamlit >/dev/null 2>&1; then
  echo "ERROR: streamlit not on PATH. Activate .venv or: pip install streamlit" >&2
  exit 1
fi

# 0.0.0.0 so phones on the LAN can reach it
exec streamlit run dashboard/app.py \
  --server.address 0.0.0.0 \
  --server.port 8501 \
  --browser.gatherUsageStats false
