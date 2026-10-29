---
name: quality-gate
description: The atdd plugin's deterministic quality gate, a Python script that /atdd:init installs into each repo as scripts/atdd/gate.py. Covers what each check measures (build analyzers, lint, tests, unbound steps, cyclomatic and cognitive complexity, diff coverage, mutation testing, duplication, architecture rules, dead code, vulnerable dependencies, suppressions, locked files), how to run it, how to read report.json, and how to turn failures into actionable findings. Also covers per-stack setup for .NET, TypeScript and Python. Use whenever running, interpreting, setting up or tuning the atdd quality gate, or explaining why it failed.
---

# The quality gate

The gate is a single Python script. Its source is in this skill: `${CLAUDE_SKILL_DIR}/scripts/gate.py`. `/atdd:init` copies it unchanged into each repo as `scripts/atdd/gate.py`, and every command below runs that copy from the repo root. A repo without `scripts/atdd/gate.py` hasn't been set up yet: run `/atdd:init`.

The copy lives in the repo on purpose:
- it's a locked path, so the implementer can't weaken it;
- CI and teammates can run it without the plugin;
- each repo keeps the version its thresholds were tuned against.

When the plugin's gate changes, copy the new `scripts/gate.py` over the repo's `scripts/atdd/gate.py` and commit it.

- It needs Python 3.9+ and nothing else of its own. It drives the stack's tools and decides pass or fail **deterministically**: same code, same verdict.
- Everything is configured in `.claude/atdd.json` → `gate`, a locked file that only humans change.

```bash
python3 scripts/atdd/gate.py fast             # the commit gate (the implementer's commit hook runs this)
python3 scripts/atdd/gate.py full             # the full gate (the quality-tester runs this)
python3 scripts/atdd/gate.py full --only mutation,diff-coverage
```

**Environment variables:**
- `ATDD_BASE`: the diff base. Defaults to the merge-base with `baseBranch`.
- `ATDD_OUT`: the output directory. Defaults to `.atdd/quality/latest`, and is cleared on every run.
- `ATDD_SCENARIOS_COMMIT`: the commit holding the approved scenarios, used by the locked-files check.

The gate exits 1 if any check fails. Its stdout ends with a summary of the failures. Everything else goes to `<out>/`.

## Clean as you code

The gate judges **the change, not the history**. Every check is scoped to what changed since the base:

- files added or modified since the base;
- lines added since the base;
- functions that contain such lines.

A legacy file with old problems doesn't fail the gate because someone touched one line in it. A function someone changed must meet the bar in full, though. The one exception is **format**: formatters work per file, so a touched file must be formatted as a whole.

## The checks

| Check | Measures | .NET | TypeScript | Python | Fails when |
|---|---|---|---|---|---|
| `build` | compiles; analyzer warnings | `dotnet build` + Roslyn/Sonar analyzers | `tsc --noEmit` | `compileall` | build error, or any warning in changed code |
| `format` | formatting | `dotnet format whitespace` | Prettier | `ruff format` | a changed file isn't formatted |
| `lint` | linter rules | (analyzers in `build`) | repo ESLint | `ruff check` | a problem on an added line |
| `tests` | all tests pass (+ coverage in `full`) | `dotnet test` (+ coverlet) | unit + acceptance (+ c8) | `pytest` (+ pytest-cov) | any test fails |
| `undefined-steps` | every Gherkin step bound exactly once | Reqnroll `missingOrPendingStepsOutcome: Error` | cucumber dry run / `bddgen` | `scenarios()` binds all | undefined, ambiguous or unbound steps |
| `complexity` | cyclomatic complexity, function length, parameters | lizard | lizard | lizard | a changed function exceeds a limit |
| `cognitive` | cognitive complexity | Sonar S3776 (in `build`) | sonarjs via `eslint.quality.mjs` | complexipy | a changed function exceeds the limit |
| `diff-coverage` | share of changed lines covered | diff-cover | diff-cover | diff-cover | below `diffCoverage`, or changed files missing from the report |
| `mutation` | share of mutants on changed lines killed | Stryker.NET `--since` | StrykerJS `--mutate <changed>` | mutmut (changed modules) | below `mutationScore` |
| `duplication` | new copy-paste vs the base | jscpd `--baseline-from-ref` | same | same | any new clone |
| `architecture` | layering and dependency rules | ArchUnitNET/NetArchTest tests | dependency-cruiser | import-linter | a rule is broken (skipped until rules exist) |
| `dead-code` | unused code introduced | IDE0051/52/60 (in `build`) | knip | vulture | unused code on added lines |
| `vulnerabilities` | known-vulnerable packages | `dotnet package list --vulnerable` | `npm audit` | `pip-audit` | vulnerable **and** this change touched dependency manifests; otherwise `warn` |
| `suppressions` | new pragmas, ignores, skips | `#pragma warning disable`, `[ExcludeFromCodeCoverage]`, `Skip =`, … | `eslint-disable`, `@ts-ignore`, `.skip(`, `.only(`, … | `# noqa`, `# type: ignore`, `pytest.mark.skip`, … | any new one |
| `locked-files` | scenarios, ledger, gate scripts and threshold configs untouched | `lockedPaths` | `lockedPaths` | `lockedPaths` | any changed after `ATDD_SCENARIOS_COMMIT` |

**Default thresholds** live in `gate.thresholds`:

| Threshold | Default |
|---|---|
| `diffCoverage` | 90 (%) |
| `mutationScore` | 80 (%) |
| `cyclomatic` | 10 |
| `cognitive` | 15 |
| `functionLength` | 60 (lines of code) |
| `parameters` | 5 |

**Which checks run in each mode** comes from `gate.fast` / `gate.full`. When unset:
- `fast`: build, format, lint, tests, undefined-steps, complexity, cognitive, suppressions.
- `full`: everything.

**Overriding a check's command.** `gate.commands.<check>` replaces the command a check runs: for example, a `testsWithCoverage` command for .NET 10 MTP repos. The override's exit code decides the result.

### Silent passes are failures

A gate that passes while measuring nothing is worse than no gate. These guards are built in:

- **Missing tool:** the check fails and names the install command.
- **No changed production code:** the check reports `skipped` with that reason, explicitly.
- **Zero mutants on the changed lines:** `skipped` with the reason, never a NaN pass.
- **Coverage that doesn't measure the change:**
  - fails when the coverage reports contain none of the changed lines (a path mismatch);
  - fails when a changed production file is missing from the reports.
- **A crashing check** fails.
- **Stale results:** the output directory is wiped at the start of every run, so leftovers from earlier runs can't count.

## Reading the results

| File in `<out>/` | Contents |
|---|---|
| `report.json` | `passed`, `base`, `head`, `changedFiles`, `thresholds`, and `checks[]` with `{name, status, metric, threshold, detail, log, seconds}`. `status` is `pass`, `fail`, `warn` or `skipped` |
| `logs/<check>.log` | full tool output, starting with the exact command |
| `build-warnings.txt` | .NET warnings in changed code |
| `uncovered-lines.txt` | changed lines no test executes, per file |
| `mutation-survivors.txt` | `file:line: mutator -> replacement [Survived/NoCoverage]`; for Python, mutmut mutant names (`mutmut show <name>`) |
| `jscpd/jscpd-report.json` | clone pairs |
| `lint.json`, `cognitive.json`, `diff-cover.json` | raw tool reports |

## Turning failures into findings

The implementer gets your findings, not the logs. Write each one so it can be fixed in one pass:

- **build (warnings).** Quote `file:line: CODE message` and say what the rule wants:
  - S3776: cognitive complexity. Flatten nesting and extract named steps.
  - CA1502: cyclomatic complexity.
  - CA1506: too many types coupled. Split responsibilities.
  - CA1505: low maintainability.
  - IDE0051/IDE0052/IDE0060: unused member, value or parameter. Delete it, or use it.
  - CS86xx: nullability. Model the null case, don't `!` it away.
- **complexity / cognitive.** Name the function, its score and the limit. Point at the constructs driving it (nested ifs, flag parameters, long `else if` chains) and suggest the extraction or early return that brings it under.
- **diff-coverage.** Group the uncovered lines by the behavior they implement, and say which test level fits. A scenario's path that isn't covered means a step definition doesn't reach it.
- **mutation.**
  - Group survivors by function.
  - For each group, state the behavior no test pins down, e.g. "no test tells a value exactly at the limit from one just above it; the `<=` → `<` mutant survives".
  - Give the concrete test case with values that would kill it.
  - `NoCoverage` mutants mean the code isn't executed by any test at all.
- **duplication.** Quote both locations. Recommend extraction only if both copies change for the same reason. Otherwise say so and let a human decide.
- **architecture.** Quote the rule (contract or ArchUnit test name) and the offending dependency, then say where the code should depend instead.
- **dead-code.** Name the unused symbol: delete it, or wire it into the feature if a scenario needs it.
- **suppressions.** Quote the line. The fix is the underlying problem, never the suppression.
- **locked-files.** List the files. They must be reverted. If the implementer believes one is wrong, that's a question for a human.
- **undefined-steps / tests.** Name the failing scenario or test and quote the first assertion message.
- **A `warn` on vulnerabilities**, or a `skipped` because rules aren't configured, is a minor finding addressed to a human, not to the implementer.

## Setting the gate up in a repo

`/atdd:init` does this. These references hold the exact steps and configs for each stack:

- `${CLAUDE_SKILL_DIR}/references/dotnet.md`
- `${CLAUDE_SKILL_DIR}/references/typescript.md`
- `${CLAUDE_SKILL_DIR}/references/python.md`

The per-stack templates sit in `${CLAUDE_SKILL_DIR}/templates/<stack>/`, the QA start and stop templates in `templates/common/` (`qa-up.sh`, `qa-down.sh`), and the gate itself in `scripts/gate.py`.

**Changing a threshold** is a human decision. Edit `gate.thresholds` in `.claude/atdd.json`. Keep the tools' own copies in step:

- .NET: `SonarLint.xml` (S3776) and `CodeMetricsConfig.txt` (CA1502);
- Python: ruff's `mccabe` and `pylint` settings.

The gate itself reads `gate.thresholds`.
