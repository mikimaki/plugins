---
tags: [atdd, gherkin]
max_turns: 200
timeout_seconds: 3600
allowed_tools: [Workflow, Agent, Read, Write, Edit, Glob, Grep, Bash]
---

/atdd:gherkin .scratch/sleep-timer/spec.md

Input files: .claude/atdd.json, .scratch/sleep-timer/spec.md, GLOSSARY.md, docs/adr/0001-streamed-episodes.md, docs/agents/issue-tracker.md, features/playback/playback.feature, features/playback/requirements.md
