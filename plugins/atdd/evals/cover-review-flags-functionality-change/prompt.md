---
tags: [atdd, cover]
max_turns: 120
timeout_seconds: 2400
allowed_tools: [Workflow, Agent, Read, Write, Edit, Glob, Grep, Bash]
---

Branch atdd/fit-7 is supposed to add acceptance tests for our existing daily step goals feature, from the approved scenarios in features/fit-7/ (spec: .atdd/fit-7/spec.md), without changing any functionality. It's the safety net we want in place before we refactor the goals code. Review what's on the branch (git diff main..HEAD) and tell me whether it's OK to merge. List every problem you find, most severe first.

Input files: .claude/atdd.json, .atdd/fit-7/issue.json, .atdd/fit-7/spec.md, GLOSSARY.md, docs/agents/issue-tracker.md, pyproject.toml, src/stepgoals/app.py, src/stepgoals/celebrations.py, src/stepgoals/goals.py, src/stepgoals/push_gateway.py, tests/unit/test_goals.py, features/fit-7/goals.feature, features/fit-7/requirements.md, tests/acceptance/conftest.py, tests/acceptance/test_features.py
