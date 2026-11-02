#!/usr/bin/env bash
# Stops what scripts/atdd/qa-up.sh started: the whole process group. Safe to run when nothing is running.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

if [[ -s .atdd/qa/app.pid ]]; then
  group="$(cat .atdd/qa/app.pid)"
  kill -TERM -- "-$group" 2>/dev/null
  for _ in $(seq 1 15); do kill -0 -- "-$group" 2>/dev/null || break; sleep 1; done
  kill -KILL -- "-$group" 2>/dev/null
  : > .atdd/qa/app.pid
fi

# Dependencies started by qa-up.sh:
# docker compose stop mysql
echo "App stopped."
