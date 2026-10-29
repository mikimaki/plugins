#!/usr/bin/env bash
set -eu
cp -R "$(dirname "$0")/files/." .
# The fixture's .claude/ folder ships as dot-claude/, because some file-sync tools refuse to write .claude folders.
mkdir -p .claude
cp -R dot-claude/. .claude/
rm -rf dot-claude
git init -q -b main 2>/dev/null || true
git add -A && git -c user.email=eval@example.com -c user.name=eval commit -qm "fixture" || true
