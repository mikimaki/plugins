#!/usr/bin/env bash
# Builds the /atdd:cover-queue end-to-end fixture: a Python app (the cover eval's stepgoals base) on main, an
# "inventory" branch with two areas of two .feature files each plus their specs, a bare origin, the integration
# branch created once on origin (the human's one-time step), and a stub gh.
#   build.sh <plugin-dir> <target-dir>
set -euo pipefail
plugin="$1"; target="$2"
here="$(cd "$(dirname "$0")" && pwd)"
base="$plugin/evals/cover-reports-discrepancy-not-code-change/files/base"
rm -rf "$target"; mkdir -p "$target"
git init -q --bare -b main "$target/origin.git"
git init -q -b main "$target/repo"
cd "$target/repo"
git config user.email fixture@example.com; git config user.name fixture
cp -R "$base/." .
rm -rf .atdd
mkdir -p .claude scripts/atdd
cp -R dot-claude/. .claude/ && rm -rf dot-claude
mv dot-gitignore .gitignore
cp "$plugin/skills/quality-gate/scripts/gate.py" scripts/atdd/gate.py
python3 - <<'PY'
import json, pathlib
p = pathlib.Path(".claude/atdd.json"); c = json.loads(p.read_text())
c["cover"] = {"integrationBranch": "atdd/cover-base", "openPrs": True, "maxConsecutiveParks": 3}
p.write_text(json.dumps(c, indent=2) + "\n")
PY
git add -A && git commit -qm "Stride: daily step goals and celebrations"
git remote add origin "$target/origin.git"
git push -q origin main
git switch -q -c inventory
cp -R "$here/inventory/." .
git add -A && git commit -qm "As-built specs and scenarios for a01-goals and a02-celebrations"
git push -q origin inventory
git switch -q main
# The human's one-time step: the queue never pushes the integration branch.
git push -q origin main:refs/heads/atdd/cover-base
git fetch -q origin
mkdir -p "$target/bin" && cp "$here/bin/gh" "$target/bin/gh"
: > "$target/gh-calls.jsonl"
echo "fixture ready: $target/repo (main), source ref: inventory"
