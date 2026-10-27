#!/bin/sh
# Pick a Streamlit entrypoint and listen on $PORT (default 7860).
set -eu

port="${PORT:-7860}"
app="${STREAMLIT_APP:-}"

if [ -z "$app" ]; then
  for candidate in app.py streamlit_app.py heatmap_app.py; do
    if [ -f "/app/$candidate" ]; then
      app="$candidate"
      break
    fi
  done
fi

if [ -z "$app" ] || [ ! -f "/app/$app" ]; then
  echo "No Streamlit entrypoint found in /app." >&2
  echo "Expected app.py, streamlit_app.py, or heatmap_app.py, or set STREAMLIT_APP." >&2
  exit 1
fi

exec streamlit run "/app/$app" \
  --server.port="$port" \
  --server.address=0.0.0.0 \
  --server.headless=true \
  --server.fileWatcherType=none \
  --browser.gatherUsageStats=false
