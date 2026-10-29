---
tags: [atdd, gherkin]
max_turns: 200
timeout_seconds: 3600
allowed_tools: [Workflow, Agent, Read, Write, Edit, Glob, Grep, Bash]
---

/atdd:gherkin .scratch/overdue-notices/spec.md

Input files: .claude/atdd.json, .scratch/overdue-notices/spec.md, GLOSSARY.md, docs/adr/0004-branch-time-zone.md, docs/agents/issue-tracker.md, features/loans/borrowing.feature, features/loans/requirements.md
