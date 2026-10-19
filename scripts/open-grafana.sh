#!/usr/bin/env bash
# Opens the monitoring dashboard. Keep this terminal open while you look at it; Ctrl+C to stop.
set -euo pipefail
PORT="${PORT:-3000}"
echo "Opening http://localhost:${PORT}   (press Ctrl+C here when you are done)"
( sleep 2; (open "http://localhost:${PORT}" 2>/dev/null || xdg-open "http://localhost:${PORT}" 2>/dev/null || true) ) &
exec kubectl -n sos port-forward svc/grafana "${PORT}:3000"
