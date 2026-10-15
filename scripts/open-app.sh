#!/usr/bin/env bash
# Opens the app in your browser. Keep this terminal open while you use the app; press Ctrl+C to stop.
set -euo pipefail
PORT="${PORT:-8080}"
echo "Opening http://localhost:${PORT}   (press Ctrl+C here when you are done)"
( sleep 2; (open "http://localhost:${PORT}" 2>/dev/null || xdg-open "http://localhost:${PORT}" 2>/dev/null || true) ) &
exec kubectl -n sos port-forward svc/web "${PORT}:80"
