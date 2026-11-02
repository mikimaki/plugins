#!/usr/bin/env bash
# Starts the Next.js app for the atdd QA agent in the background, then returns. The QA agent polls qa.healthUrl
# from .claude/atdd.json until the app answers. /atdd:init adapts this template to the repo; keep it idempotent.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
mkdir -p .atdd/qa

# The database, when the repo runs MySQL in Docker for local development:
# docker compose up -d --wait mysql

# The development database from .env, migrated the way production will be:
npx prisma migrate deploy
# Data a tester needs, when the repo has a seed (prisma.config.ts → migrations.seed):
# npx prisma db seed

# Its own process group (set -m), so qa-down.sh stops npm, Next.js and its workers together.
set -m
nohup bash -c "npm run build && npm run start -- --port 3000" > .atdd/qa/app.log 2>&1 &
echo $! > .atdd/qa/app.pid
echo "App building and starting (pid $(cat .atdd/qa/app.pid)); log: .atdd/qa/app.log"
