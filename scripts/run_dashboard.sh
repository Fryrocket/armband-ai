#!/usr/bin/env bash
# Launch the live dashboard (accessible from phone on the same network)
set -e
cd "$(dirname "$0")/.."

if [ -d .venv ]; then
  source .venv/bin/activate
fi

# 0.0.0.0 so phones on the LAN can reach it
exec streamlit run dashboard/app.py \
  --server.address 0.0.0.0 \
  --server.port 8501 \
  --browser.gatherUsageStats false
