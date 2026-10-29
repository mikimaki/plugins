# Quality gate setup: TypeScript / Node

Templates are in `${CLAUDE_SKILL_DIR}/templates/typescript/`. These versions were verified end-to-end in October 2026: Node 22, TypeScript 6.0, Cucumber 13.3, StrykerJS 10.1, ESLint 10, eslint-plugin-sonarjs 4.2, c8 12, dependency-cruiser 18.7, knip 6.43 and jscpd 5.4.2.

## Prerequisites

- **Node 22+.** Cucumber 13, Stryker 10 and dependency-cruiser 18 dropped Node 20.
- **TypeScript 6.0.x.** typescript-eslint and sonarjs don't support TypeScript 7 yet.
- **Python 3.9+** for `gate.py`, plus lizard and diff-cover: `pipx install lizard==1.24.3 && pipx install diff-cover`.

## Dev dependencies

```bash
npm i -D @cucumber/cucumber tsx c8 \
  @stryker-mutator/core @stryker-mutator/typescript-checker @stryker-mutator/vitest-runner \
  eslint typescript-eslint eslint-plugin-sonarjs dependency-cruiser knip
```

- **Stryker runner:** use the one for the repo's unit-test framework: `vitest-runner`, `jest-runner` or `mocha-runner`. A repo with only Cucumber tests uses `@stryker-mutator/cucumber-runner`, with `"testRunner": "cucumber"` and, for tsx/ESM, `"testRunnerNodeArgs": ["--import", "tsx"]`.
- **Prettier:** add it if the repo formats with it. Without it, the format check is skipped.
- **jscpd:** don't add it. The gate runs a pinned `npx jscpd@5.4.2`.

## Files to add or merge

| Template | Goes to | Notes |
|---|---|---|
| `atdd.json` | `.claude/atdd.json` | Set `featuresDir`, `gate.sourceDirs`, the commands (`unitTests`, `acceptanceTests`) and `gate.acceptanceRunner` (`cucumber` or `playwright-bdd`) |
| `eslint.quality.mjs` | `scripts/atdd/` | Cognitive complexity only, independent of the repo's ESLint config. The threshold comes from the gate |
| `tsx-register.js` | `scripts/atdd/` | Lets Cucumber load TypeScript step definitions (ESM) |
| `cucumber.mjs` | repo root | `strict: true`. Adjust `paths`/`import` to `featuresDir` and the step folders |
| `stryker.config.json` | repo root | `break: null`, because the gate computes the score itself over changed lines; the JSON report goes to `reports/mutation/mutation.json` |
| `dependency-cruiser.cjs` | `.dependency-cruiser.cjs` | Generic rules, plus a commented example to replace with the repo's real layering |
| `${CLAUDE_SKILL_DIR}/scripts/gate.py` | `scripts/atdd/gate.py` | The gate itself. Copy it unchanged |
| `../common/qa-up.sh`, `../common/qa-down.sh` | `scripts/atdd/` | Adapt `qa-up.sh` (e.g. `PORT=3000 npm run start`) |

Add `reports/`, `coverage/`, `.stryker-tmp/` and `.atdd/` to `.gitignore`.

## How the gate drives the tools

- **Build:** `npx tsc --noEmit`. Any type error fails.
- **Lint:** the repo's ESLint (`-f json`). Only problems on added lines count.
- **Cognitive:** `eslint -c scripts/atdd/eslint.quality.mjs -f json <changed files>`, with the threshold passed in `ATDD_COGNITIVE`. A report inside a changed function counts.
- **Tests (full mode):** each test command runs under `c8 --reporter=cobertura --all --include <sourceDirs>/**`, so untested files still appear in the report.
- **Undefined steps:**
  - Cucumber: `cucumber-js --dry-run --format message:…`, then the gate counts `UNDEFINED`/`AMBIGUOUS` statuses. A dry run always exits 0, so its exit code is useless.
  - playwright-bdd: `bddgen`, which fails on missing steps by default.
- **Mutation:** `stryker run --mutate <changed production files> --incremental --incrementalFile .atdd/cache/stryker-incremental.json`. The score counts only mutants on changed lines.
- **Architecture:** `depcruise <sourceDirs> -T json`. The gate reads `summary.error` rather than the exit code, which wraps around at 256.
- **Dead code:** `knip --reporter json`, filtered to items on added lines, plus unused files this change added.
