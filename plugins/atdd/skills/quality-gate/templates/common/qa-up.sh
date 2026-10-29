#!/usr/bin/env bash
# Starts the app (and its dependencies) for the atdd QA agent, in the background, then returns.
# The QA agent polls qa.healthUrl from .claude/atdd.json until the app answers.
# /atdd:init adapts this template to the repo; keep it idempotent and quick to return.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
mkdir -p .atdd/qa

# Dependencies (database, queues, ...), if the repo has a compose file for local development:
# docker compose -f docker-compose.yml up -d --wait

# The app itself. Pick the line for this stack and adapt it:
#   .NET:       ASPNETCORE_ENVIRONMENT=Development dotnet run --project src/__App__ --urls http://localhost:5080
#   TypeScript: PORT=3000 npm run start
#   Python:     uvicorn myapp.main:app --port 8000
APP_COMMAND="echo 'Edit scripts/atdd/qa-up.sh: set APP_COMMAND to start the app' >&2; exit 1"

nohup bash -c "$APP_COMMAND" > .atdd/qa/app.log 2>&1 &
echo $! > .atdd/qa/app.pid
echo "App starting (pid $(cat .atdd/qa/app.pid)); log: .atdd/qa/app.log"
