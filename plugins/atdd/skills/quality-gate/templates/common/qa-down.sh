#!/usr/bin/env bash
# Stops what scripts/atdd/qa-up.sh started. Safe to run when nothing is running.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

if [[ -f .atdd/qa/app.pid ]]; then
  pid="$(cat .atdd/qa/app.pid)"
  pkill -TERM -P "$pid" 2>/dev/null
  kill -TERM "$pid" 2>/dev/null
  for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
  kill -KILL "$pid" 2>/dev/null
  : > .atdd/qa/app.pid
fi

# Dependencies started by qa-up.sh:
# docker compose -f docker-compose.yml down
echo "App stopped."
