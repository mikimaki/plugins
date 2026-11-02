# Quality gate setup: TypeScript / Node

Templates are in `${CLAUDE_SKILL_DIR}/templates/typescript/`. These versions were verified end-to-end in October 2026: Node 22, TypeScript 6.0, Cucumber 13.3, Vitest 5.0, StrykerJS 10.1, ESLint 10, eslint-plugin-sonarjs 4.2, monocart-coverage-reports 3.2, vitest-monocart-coverage 5.0, dependency-cruiser 18.7, knip 6.44 and jscpd 5.4.2.

For a Next.js app, follow `nextjs.md` instead. It uses the same scripts, with Playwright in place of Cucumber.

## Prerequisites

- **Node 22+.** Cucumber 13, Stryker 10 and dependency-cruiser 18 dropped Node 20.
- **TypeScript 6.0.x.** typescript-eslint and sonarjs don't support TypeScript 7 yet.
- **Python 3.9+** for `gate.py`, plus lizard and diff-cover: `pipx install lizard==1.24.3 && pipx install diff-cover`.

## Dev dependencies

```bash
npm i -D @cucumber/cucumber tsx vitest @vitest/coverage-v8 monocart-coverage-reports vitest-monocart-coverage \
  @stryker-mutator/core @stryker-mutator/typescript-checker @stryker-mutator/vitest-runner \
  eslint typescript-eslint eslint-plugin-sonarjs dependency-cruiser knip
```

- **Vitest and its coverage packages:** keep `@vitest/coverage-v8` on the same version as `vitest`. vitest-monocart-coverage builds on it.
- **Stryker runner:** use the one for the repo's unit-test framework: `vitest-runner`, `jest-runner` or `mocha-runner`. A repo with only Cucumber tests uses `@stryker-mutator/cucumber-runner`, with `"testRunner": "cucumber"` and, for tsx/ESM, `"testRunnerNodeArgs": ["--import", "tsx"]`.
- **Prettier:** add it if the repo formats with it. Without it, the format check is skipped.
- **jscpd:** don't add it. The gate runs a pinned `npx jscpd@5.4.2`.
- **c8:** not needed any more. A repo set up with it can uninstall it: c8 can't see code that Vitest runs.

## Files to add or merge

| Template | Goes to | Notes |
|---|---|---|
| `atdd.json` | `.claude/atdd.json` | Set `featuresDir`, `gate.sourceDirs`, the commands (`unitTests`, `acceptanceTests`) and `gate.acceptanceRunner` (`cucumber` or `playwright-bdd`) |
| `eslint.quality.mjs` | `scripts/atdd/` | Cognitive and NPath complexity only, independent of the repo's ESLint config. The thresholds come from the gate |
| `eslint-npath.mjs` | `scripts/atdd/` | The NPath rule, `atdd/npath-complexity`, which `eslint.quality.mjs` loads |
| `coverage.mjs` | `scripts/atdd/` | Records V8 coverage while the gate measures it and merges every run into one Cobertura report |
| `cobertura-reporter.mjs` | `scripts/atdd/` | Writes that report, with line and branch counts |
| `tsx-register.js` | `scripts/atdd/` | Lets Cucumber load TypeScript step definitions (ESM) |
| `cucumber.mjs` | repo root | `strict: true`. Adjust `paths`/`import` to `featuresDir` and the step folders |
| `stryker.config.json` | repo root | `break: null`, because the gate computes the score itself over changed lines; the JSON report goes to `reports/mutation/mutation.json` |
| `dependency-cruiser.cjs` | `.dependency-cruiser.cjs` | Generic rules, plus a commented example to replace with the repo's real layering |
| `${CLAUDE_SKILL_DIR}/scripts/gate.py` | `scripts/atdd/gate.py` | The gate itself. Copy it unchanged |
| `../common/qa-up.sh`, `../common/qa-down.sh` | `scripts/atdd/` | Adapt `qa-up.sh` (e.g. `PORT=3000 npm run start`) |

Add this coverage block to the repo's Vitest config. Nothing is recorded unless the gate is measuring coverage:

```ts
// vitest.config.mts
import { defineConfig } from 'vitest/config'
import { coverageDir, unitCoverageOptions } from './scripts/atdd/coverage.mjs'

export default defineConfig({
  test: {
    coverage: {
      enabled: coverageDir !== null,
      provider: 'custom',
      customProviderModule: 'vitest-monocart-coverage',
      ...(coverageDir && { coverageReportOptions: unitCoverageOptions() }),
    },
  },
})
```

Add `vitest.config.*` to `lockedPaths`, since it now holds coverage settings. Vitest loads `vitest-monocart-coverage` by name, so add it to knip's `ignoreDependencies`. Add `reports/`, `coverage/`, `.stryker-tmp/` and `.atdd/` to `.gitignore`.

## How the gate drives the tools

- **Build:** `npx tsc --noEmit`. Any type error fails.
- **Lint:** the repo's ESLint (`-f json`). Only problems on added lines count.
- **Cognitive and NPath:** `eslint -c scripts/atdd/eslint.quality.mjs -f json <changed files>`, with the thresholds passed in `ATDD_COGNITIVE` and `ATDD_NPATH`. A report inside a changed function counts.
- **Tests (full mode):** the gate sets `ATDD_COVERAGE_DIR=<out>/coverage` and runs each test command:
  - Vitest records through vitest-monocart-coverage, configured above;
  - every other command (Cucumber, Mocha, `node --test`) runs with `NODE_V8_COVERAGE`, so Node records it;
  - `node scripts/atdd/coverage.mjs merge` then merges both into `<out>/coverage/merged/cobertura-coverage.xml`. A line or branch is covered when any run covered it.

  Only files in `gate.sourceDirs` are reported, minus tests and generated code. A file no test loads is missing from the report, and the diff-coverage check fails on it. Jest runs code in its own module sandbox and wasn't verified with this setup; prefer Vitest.
- **Undefined steps:**
  - Cucumber: `cucumber-js --dry-run --format message:…`, then the gate counts `UNDEFINED`/`AMBIGUOUS` statuses. A dry run always exits 0, so its exit code is useless.
  - playwright-bdd: `bddgen`, which fails on missing steps by default.
- **Dependency cycles:** `depcruise <sourceDirs> --output-type json`, with the repo's `.dependency-cruiser.cjs` for module resolution (a minimal config when there is none). The gate builds two graphs from it: folders, and workspace packages (folders with a `package.json`, including their declared dependencies on each other).
- **Mutation:** `stryker run --mutate <changed production files> --incremental --incrementalFile .atdd/cache/stryker-incremental.json`. The score counts only mutants on changed lines.
- **Architecture:** `depcruise <sourceDirs> -T json`. The gate reads `summary.error` rather than the exit code, which wraps around at 256.
- **Dead code:** `knip --reporter json`, filtered to items on added lines, plus unused files this change added.
