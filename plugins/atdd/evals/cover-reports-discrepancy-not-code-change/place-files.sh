#!/usr/bin/env bash
# Builds a repo whose main branch is the existing app, with branch atdd/fit-12 holding the approved scenarios. One of
# them expects a refusal message the existing code doesn't produce.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
commit() { git add -A && git -c user.email=eval@example.com -c user.name=eval commit -qm "$1"; }
cp -R "$here/files/base/." .
# The fixture's .claude/ folder ships as dot-claude/, because some file-sync tools refuse to write .claude folders.
# Its .gitignore ships as dot-gitignore, so that the plugin's own repo doesn't ignore the fixture's .atdd/ files.
mkdir -p .claude scripts/atdd
cp -R dot-claude/. .claude/
rm -rf dot-claude
mv dot-gitignore .gitignore
cp "$here/../../skills/quality-gate/scripts/gate.py" scripts/atdd/gate.py
git init -q -b main 2>/dev/null || true
commit "Daily step goals"
git checkout -q -b atdd/fit-12
cp -R "$here/files/scenarios/." .
commit "test(acceptance): approved scenarios for fit-12"
