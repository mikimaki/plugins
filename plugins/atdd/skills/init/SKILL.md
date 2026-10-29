---
name: init
description: Set up the current repository for the atdd pipeline. Detects the stack (.NET, TypeScript or Python), writes .claude/atdd.json, installs the quality gate (scripts/atdd/gate.py with the stack's tool configs), the QA start/stop scripts and the Gherkin runner config, and checks that Matt Pocock's issue-tracker setup is in place. Run once per repo before /atdd:gherkin and /atdd:build.
disable-model-invocation: true
argument-hint: "[stack: dotnet | typescript | python]"
---

# Set up this repo for the atdd pipeline

The pipeline needs four things in the repo:

- a config: `.claude/atdd.json`;
- a deterministic gate: `scripts/atdd/gate.py` plus tool configs;
- a way to start the app for QA: `scripts/atdd/qa-up.sh` / `qa-down.sh`;
- a strict Gherkin runner.

Your job is to install these so they fit **this** repo. Copying templates blindly produces a gate that fails for the wrong reasons, and everyone learns to ignore it.

The gate, its templates and the per-stack instructions live in the quality-gate skill:

- `${CLAUDE_PLUGIN_ROOT}/skills/quality-gate/scripts/gate.py`: the gate itself;
- `${CLAUDE_PLUGIN_ROOT}/skills/quality-gate/templates/`
- `${CLAUDE_PLUGIN_ROOT}/skills/quality-gate/references/<stack>.md`, which has exact files, versions and commands. Follow it.
- `${CLAUDE_PLUGIN_ROOT}/skills/gherkin-binding/references/<stack>.md`: how the acceptance tests bind the scenarios.

## 1. Survey before asking

Find out:

- **The stack.** `*.sln`/`*.csproj` means dotnet; `package.json` with `tsconfig.json` means typescript; `pyproject.toml`, `setup.py` or `requirements*.txt` means python. `$ARGUMENTS` can name it. The gate handles one stack per repo, so in a mixed repo ask which part the pipeline covers.
- **The base branch:** `git symbolic-ref refs/remotes/origin/HEAD`, or ask.
- **Existing tests:** the test frameworks and projects, any existing acceptance or BDD setup (Reqnroll/SpecFlow, Cucumber, pytest-bdd, `.feature` files), and where tests live.
- **Existing quality tooling:** `.editorconfig`, `Directory.Build.props`, Central Package Management, ESLint and Prettier configs, ruff and pytest config, Stryker or mutmut config, coverage settings.
- **How the app starts locally:** README, `docker-compose*.yml`, launch settings, `package.json` scripts, a Procfile. Also its port and its health endpoint.
- **The docs:** `docs/architecture.md`, `docs/adr/`, `GLOSSARY.md`.
- **Matt Pocock's setup:** `docs/agents/issue-tracker.md`, and an `## Agent skills` block in `CLAUDE.md`/`AGENTS.md`.
  - If they're missing, say that `/atdd:gherkin` fetches specs through them, and recommend running `/setup-matt-pocock-skills` first.
  - The pipeline still works with spec file paths without it.

## 2. Ask only what you couldn't find

Use AskUserQuestion, at most 4 questions in one round, and offer the values you found as the recommended options. Typical gaps:

- **Where scenarios live.**
  - .NET: inside the acceptance test project, e.g. `tests/<App>.Acceptance/Features`; create the project if there is none.
  - TypeScript/Python: `features/`.
- **How QA starts the app**, and what test data or accounts a manual tester needs.
- **Whether there's a UI to test in a browser.** If yes, offer to add the Playwright MCP server for this repo only:

  ```bash
  claude mcp add --scope local playwright -- npx @playwright/mcp@0.0.83
  ```

  The server loads in new sessions.
- **The thresholds.** Defaults: diff coverage 90%, mutation score 80%, cyclomatic complexity 10, cognitive complexity 15, function length 60 lines, 5 parameters.

## 3. Install

Follow `references/<stack>.md`. In every case:

1. **Write `.claude/atdd.json`** from `templates/<stack>/atdd.json`:
   - Replace every placeholder (`__App__`, paths, commands) with the real values.
   - `lockedPaths` must cover:
     - the feature files and ledgers;
     - `scripts/atdd/**`;
     - `.claude/atdd.json` itself;
     - every file that holds a threshold or a strictness setting.

     A threshold the implementer can edit isn't a threshold.
2. **Copy into `scripts/atdd/`:**
   - the gate, `${CLAUDE_PLUGIN_ROOT}/skills/quality-gate/scripts/gate.py`, unchanged, as `scripts/atdd/gate.py`;
   - `${CLAUDE_PLUGIN_ROOT}/skills/quality-gate/templates/common/qa-up.sh` and `qa-down.sh`, adapted so `qa-up` really starts this app in the background and `qa-down` really stops it.
   - Make the scripts executable.
3. **Merge the stack's tool configs. Never clobber what's there:**
   - append to `.editorconfig`;
   - merge `Directory.Build.props` and `pyproject.toml` tables;
   - keep the existing ESLint config and add the separate `scripts/atdd/eslint.quality.mjs`.

   If an existing setting conflicts with the gate (e.g. a lower Stryker break threshold), show both and ask which wins.
4. **Make the Gherkin runner strict:**
   - Reqnroll: `reqnroll.json` with `missingOrPendingStepsOutcome: Error`;
   - Cucumber: `strict: true`;
   - pytest-bdd: `scenarios(...)` binding.
5. **Install the tools**, pinned:
   - `dotnet tool restore` with `.config/dotnet-tools.json`;
   - `npm i -D …`;
   - the repo's Python dev dependencies.

   Ask before installing system-level tools. Tell the user about `pipx install lizard==1.24.3 diff-cover` if they're missing.
6. **Ignore the gate's outputs** in `.gitignore`: `.atdd/`, plus the stack's tool output dirs (`reports/`, `coverage/`, `.stryker-tmp/`, `mutants/`, `StrykerOutput/`).
7. **Add an `## ATDD pipeline` block** to `CLAUDE.md`, or `AGENTS.md` if that's what the repo uses; never create a second file. Keep it to a few lines: where the config is, the two commands (`/atdd:gherkin <issue>`, then `/atdd:build <issue>`), and the gate commands. If the block exists, update it in place.

## 4. Prove it works

1. Run `python3 scripts/atdd/gate.py fast` on the current branch. With no changes, most checks report `skipped`, which is fine. What matters is that nothing reports a missing tool or a crashed check. Fix what does.
2. If the repo has existing `.feature` files, run the acceptance tests once to confirm the strict runner still passes.
3. Run `bash scripts/atdd/qa-up.sh`, poll the health URL, then run `bash scripts/atdd/qa-down.sh`. QA is useless if this doesn't work.

## 5. Report

Tell the user, briefly:

- what you added or changed, file by file;
- what they should review and commit (all of it, in one commit, before the first `/atdd:build`; the pipeline expects a clean tree);
- what's still missing, typically:
  - architecture rules: ArchUnitNET tests, dependency-cruiser rules or import-linter contracts. The architecture check stays `skipped` until they exist.
  - the Matt Pocock issue-tracker setup;
  - anything in `qa.notes`.
