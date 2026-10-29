# personal-skills

Daniel's personal Claude Code marketplace.

```text
/plugin marketplace add ~/personal-skills
/plugin install atdd@personal-skills
```

| Plugin | What it does |
|---|---|
| [`atdd`](plugins/atdd/README.md) | Gherkin-first ATDD pipeline: `/atdd:gherkin` (spec → reviewed scenarios), then `/atdd:build` (implementer → reviewer → QA → quality gate, looping until clean) |

After editing a plugin, run `/plugin marketplace update personal-skills`, or develop against the folder directly with `claude --plugin-dir ~/personal-skills/plugins/<name>`.
