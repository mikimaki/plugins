---
name: quality-gate
description: The atdd plugin's deterministic quality gate, a Python script that /atdd:init installs into each repo as scripts/atdd/gate.py. Covers what each check measures (build analyzers, lint, tests, unbound steps, cyclomatic, cognitive and NPath complexity, dependency cycles between namespaces or projects, line and branch coverage of changed code, mutation testing, duplication, architecture rules, dead code, vulnerable dependencies, suppressions, locked files), its cover mode for /atdd:cover (nothing outside the test paths changed, test-code quality, the acceptance tests' own coverage and mutation score on the production code they protect), how to run it, how to read report.json, and how to turn failures into actionable findings. Also covers per-stack setup for .NET, TypeScript, Next.js (React, Prisma, MySQL) and Python. Use whenever running, interpreting, setting up or tuning the atdd quality gate, or explaining why it failed.
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
python3 scripts/atdd/gate.py cover-fast       # /atdd:cover's commit gate (the coverer's commit hook runs this)
python3 scripts/atdd/gate.py cover-full       # /atdd:cover's full gate (the coverage-quality-tester runs this)
python3 scripts/atdd/gate.py --version        # atdd gate 0.4.0; /atdd:cover-queue needs 0.4.0 or later
```

**Environment variables:**
- `ATDD_BASE`: the diff base. Without it, the gate uses:
  - on a `/atdd:cover-queue` unit branch, where the unit leaves its stack parent (`git config branch.<branch>.atddBase`), then the integration branch once that parent is merged and deleted, so each unit is judged on its own change;
  - otherwise the merge-base with `baseBranch`.

  Of a local branch and its `origin/` copy, it uses the one that contains the other, so a local `baseBranch` that's behind `origin` doesn't make already-merged files count as changed. When they differ, the gate says so (`Base: …`) and records it as `baseNote` in `report.json`.
- `ATDD_OUT`: the output directory. Defaults to `.atdd/quality/latest`, and is cleared on every run.
- `ATDD_SCENARIOS_COMMIT`: the commit holding the approved scenarios, used by the locked-files check.
- `ATDD_COVER_TARGETS`: cover modes only. A file listing, one per line, the production files the acceptance tests protect.

The gate exits 1 if any check fails. Its stdout ends with a summary of the failures. Everything else goes to `<out>/`.

## Clean as you code

The gate judges **the change, not the history**. Every check is scoped to what changed since the base:

- files added or modified since the base;
- lines added since the base;
- functions that contain such lines.

A legacy file with old problems doesn't fail the gate because someone touched one line in it. A function someone changed must meet the bar in full, though. Two exceptions:

- **format:** formatters work per file, so a touched file must be formatted as a whole.
- **dependency-cycles:** a cycle is a property of the whole code base, so any cycle fails the gate, including one that was there before the change. The report says which cycles the change touches and which were already in the code base. `/atdd:init` lists the existing ones, so the team knows what to break before the first `/atdd:build`.

## The checks

The TypeScript column covers Next.js too; where Next.js differs, the cell says so.

| Check | Measures | .NET | TypeScript / Next.js | Python | Fails when |
|---|---|---|---|---|---|
| `build` | compiles; analyzer warnings | `dotnet build` + Roslyn/Sonar analyzers | `tsc --noEmit` (Next.js with Prisma: `prisma generate` first) | `compileall` | build error, or any warning in changed code |
| `format` | formatting | `dotnet format whitespace` | Prettier | `ruff format` | a changed file isn't formatted |
| `lint` | linter rules | (analyzers in `build`) | repo ESLint | `ruff check` | a problem on an added line |
| `tests` | all tests pass (+ coverage in `full`) | `dotnet test` (+ coverlet, JSON and Cobertura) | unit + acceptance (+ V8 coverage merged by `coverage.mjs`; Next.js: Vitest, the Next.js server and Chromium) | `pytest` (+ pytest-cov with branches) | any test fails, unless every failure is a flaky test (Python): then `flaky`, which doesn't fail the gate |
| `undefined-steps` | every Gherkin step bound exactly once | Reqnroll `missingOrPendingStepsOutcome: Error` | cucumber dry run / `bddgen` (Next.js: `bddgen`) | `scenarios()` binds all | undefined, ambiguous or unbound steps |
| `complexity` | cyclomatic complexity, function length, parameters | lizard | lizard | lizard | a changed function exceeds a limit |
| `cognitive` | cognitive complexity | Sonar S3776 (in `build`) | sonarjs via `eslint.quality.mjs` | complexipy | a changed function exceeds the limit |
| `npath` | NPath complexity: the number of acyclic paths through a function | `scripts/atdd/dotnet-quality` (Roslyn from the SDK) | `atdd/npath-complexity` in `eslint.quality.mjs` | computed in `gate.py` (`ast`) | a changed function exceeds `npath` |
| `dependency-cycles` | cycles between namespaces and between projects, in all production code | Roslyn namespace graph + `ProjectReference`s | dependency-cruiser graph: folders, workspace packages | `gate.py` import graph: packages, `pyproject.toml` projects | **any** cycle, new or old |
| `diff-coverage` | share of changed lines covered, and of the branches on them | diff-cover + coverlet branches | diff-cover + Cobertura branches | diff-cover + coverage.py branches | below `diffCoverage` or `diffBranchCoverage`, changed files missing from the report, or no branch data |
| `mutation` | share of mutants on changed lines killed | Stryker.NET `--since` | StrykerJS `--mutate <changed>` | mutmut (changed modules) | below `mutationScore` |
| `duplication` | new copy-paste vs the base | jscpd `--baseline-from-ref` | same | same | any new clone |
| `architecture` | layering and dependency rules | ArchUnitNET/NetArchTest tests | dependency-cruiser | import-linter | a rule is broken (skipped until rules exist) |
| `dead-code` | unused code introduced | IDE0051/52/60 (in `build`) | knip | vulture | unused code on added lines |
| `vulnerabilities` | known-vulnerable packages | `dotnet package list --vulnerable` | `npm audit` (Next.js with `pnpm-lock.yaml`: `pnpm audit`) | `pip-audit` | vulnerable **and** this change touched dependency manifests; otherwise `warn` |
| `suppressions` | new pragmas, ignores, skips | `#pragma warning disable`, `[ExcludeFromCodeCoverage]`, `Skip =`, … | `eslint-disable`, `@ts-ignore`, `.skip(`, `.only(`, `v8 ignore`, … (Next.js also `.fixme(`, `test.fail(`, `.todo(`) | `# noqa`, `# type: ignore`, `pytest.mark.skip`, … | any new one |
| `locked-files` | scenarios, ledger, gate scripts and threshold configs untouched | `lockedPaths` | `lockedPaths` | `lockedPaths` | any changed after `ATDD_SCENARIOS_COMMIT` |

**Flaky tests (Python).** When pytest fails, the gate classifies each failing test from its JUnit report, without re-running anything. The test's module comes from the JUnit classname (pytest-bdd reports its own `scenario.py` as the file). A failing test is **flaky** when both hold:

- its test side exists unchanged on the base commit: the test module, every `conftest.py` above it, every test-side module (helpers, step modules, `pytest_plugins`) those files import directly or transitively, and every `.feature` file their `scenarios()` and `scenario()` calls bind (resolved against `bdd_features_base_dir`; a feature named by anything but a string literal binds every changed `.feature` file);
- nothing on that test side imports, through the Python import graph, a production file of `sourceDirs` this branch changed.

Every other failure stays a failure, in every mode, cover modes included. A file the import walk needs but can't read or parse makes the failure a plain one, and the detail names the file. When every failure is flaky, the `tests` check reports `flaky`: the gate passes, and the detail names each test and why it qualified. In `full` mode, coverage is measured in a second pytest run that deselects the flaky tests (if that run fails, `tests` fails), and mutmut runs with them deselected too (`PYTEST_ADDOPTS=--deselect …`); `diff-coverage` and `mutation` name the excluded tests in their detail. Fast mode classifies the same way, so the commit hook doesn't refuse a commit for a flaky test. The gate never quarantines or skips a test outside its own run. The rule assumes the base is green: a test that is always broken on the base surfaces as `flaky` on every branch that doesn't touch its code, until a human fixes it. Other stacks don't classify yet; any failure is `fail`.

**Default thresholds** live in `gate.thresholds`:

| Threshold | Default |
|---|---|
| `diffCoverage` | 100 (% of changed lines) |
| `diffBranchCoverage` | 100 (% of the branches on changed lines) |
| `mutationScore` | 80 (%) |
| `cyclomatic` | 10 |
| `cognitive` | 15 |
| `npath` | 200 (paths per function) |
| `functionLength` | 60 (lines of code) |
| `parameters` | 5 |

Zero dependency cycles isn't a threshold: it can't be configured.

**Which checks run in each mode** comes from `gate.fast` / `gate.full`. When unset:
- `fast`: build, format, lint, tests, undefined-steps, complexity, cognitive, npath, dependency-cycles, suppressions, duplication, architecture, dead-code, locked-files.
- `full`: those, plus diff-coverage, mutation and vulnerabilities.

Duplication, architecture, dead-code and locked-files took about a second or less each on the TypeScript, Python and .NET fixtures, so the commit hook runs them too. Duplication needs Node.js (`npx jscpd`) on every stack. A repo's `gate.fast` list replaces these defaults entirely.

**Overriding a check's command.** `gate.commands.<check>` replaces the command a check runs: for example, a `testsWithCoverage` command for .NET 10 MTP repos, or `npath` and `dependencyCycles` for a tool of the repo's own. The override's exit code decides the result.

### What NPath, cycles and branch coverage mean here

**NPath** (Nejmeh, 1988) counts the acyclic execution paths through a function. Decisions in sequence multiply: eight independent `if`s make 2⁸ = 256 paths, while cyclomatic complexity only says 9. Every stack uses the rules PMD and Checkstyle document:
- an `if` adds its branches' paths, plus one for a missing `else`;
- loops add one path for not running;
- a `switch` adds its cases;
- `&&`, `||`, `??` (and their assignment forms) and C#'s `and`/`or` patterns add one each, and `?:` adds two;
- `try` adds its `catch` blocks; `finally` multiplies.

Nested functions and lambdas are measured on their own.

**A dependency cycle** is a loop in the "depends on" graph of production code (tests and generated code are left out):

| Stack | A namespace is | A project is | A depends on B when |
|---|---|---|---|
| .NET | a C# namespace | a `*.csproj` | code in A uses a type declared in B; a `ProjectReference` |
| TypeScript / Next.js | a folder | a workspace package (a folder with a `package.json`) | a module in A imports one in B; a declared dependency on B |
| Python | a package | a folder with a `pyproject.toml`, `setup.py` or `setup.cfg` | a module in A imports one in B, wherever the import sits |

The report names one shortest loop per cycle, with the file (and, for .NET and Python, the line) behind every hop, plus all members when the tangle is larger than the loop.

**Branch coverage** counts the branches that start on changed lines and checks that tests take each one. A branch is code that runs only sometimes: either side of an `if` or `?:`, the right of `&&`/`||`/`??`, a `case`. The tools differ slightly:
- coverage.py and coverlet also count an `if`'s missing `else`;
- V8 (TypeScript, Next.js) doesn't, but still counts an `if` whose body never ran.

The tests can come from several runs: .NET runs one per test project, TypeScript and Next.js add Vitest, Cucumber or Playwright, the Next.js server and the browser. A line and its branches count as covered when any run covered them:
- TypeScript and Next.js merge all V8 coverage before converting it;
- .NET adds up coverlet's JSON reports, which name every branch;
- with Cobertura reports only (a `testsWithCoverage` override), the best report for each line counts, because Cobertura doesn't say which branches ran.

### Silent passes are failures

A gate that passes while measuring nothing is worse than no gate. These guards are built in:

- **Missing tool:** the check fails and names the install command.
- **No changed production code:** the check reports `skipped` with that reason, explicitly.
- **Zero mutants on the changed lines:** `skipped` with the reason, never a NaN pass.
- **Coverage that doesn't measure the change:**
  - fails when the coverage reports contain none of the changed lines (a path mismatch);
  - fails when a changed production file is missing from the reports;
  - fails when reports from a `testsWithCoverage` override carry no branch data at all.
- **Unmeasurable code:** a changed file the NPath parser can't read fails the check instead of being skipped, and so does a dependency-cruiser graph that contains none of the production files.
- **A crashing check** fails.
- **Stale results:** the output directory is wiped at the start of every run, so leftovers from earlier runs can't count.

## Cover mode

`/atdd:cover` adds acceptance tests to code that already exists and may change nothing else. Its changes are tests only, so the gate judges them differently:

- **The test code is what gets measured.** `complexity`, `cognitive`, `npath` and `dead-code` look at the changed test code in the cover modes, where they'd look at production code otherwise. `format`, `lint`, `duplication` and `suppressions` cover test files in every mode.
- **Production code isn't judged at all.** `dependency-cycles`, `architecture`, `diff-coverage`, `mutation` and `vulnerabilities` don't run. So a repo whose existing code has dependency cycles can be covered with acceptance tests before anyone breaks the cycles, and should be: the tests are what make breaking them safe.
- **Four checks exist only in cover mode:**

| Check | Measures | Fails when |
|---|---|---|
| `production-untouched` | every file that differs from the base, committed or not (added, modified, deleted, both sides of a rename, untracked), against the test paths | any file outside the test paths differs, or the test paths are missing or would match production files |
| `duplicate-steps` | every step definition in the test paths, changed or not: Reqnroll attributes, Cucumber.js and playwright-bdd `Given`/`When`/`Then` calls, pytest-bdd decorators. For pytest-bdd it also matches every step of the working tree's `.feature` files against them | a step text (or pattern) is defined more than once, or, in pytest-bdd, a step matches more than one definition |
| `acceptance-coverage` | runs the acceptance tests alone with coverage, and reports the lines and branches of the coverage targets they never run | a target is missing from the report, or no acceptance test runs a single line of it; gaps are `warn` |
| `acceptance-mutation` | mutates the coverage targets and runs only the acceptance tests against each mutant | the run crashes or writes no report; mutants they don't notice are `warn` |

**The test paths** are `cover.testPaths` in `.claude/atdd.json` (globs), or else every `gate.testDirs` folder plus `featuresDir`. `scripts/atdd_guard.py` uses the same paths to keep the acceptance-test-coverer's edits and commits inside them.

**Which checks run in each cover mode** comes from `gate.coverFast` / `gate.coverFull`. When unset:

- `cover-fast`: production-untouched, build, format, lint, tests, undefined-steps, duplicate-steps, complexity, cognitive, npath, suppressions.
- `cover-full`: those, plus duplication, locked-files, acceptance-coverage and acceptance-mutation.

**Why duplicate steps get a check of their own.** Covering existing code goes feature by feature, and the same Givens recur in every area. The runners differ on what a second definition does:

- bddgen (playwright-bdd) and Cucumber.js fail on a step two definitions match, whatever their keywords, but only when a feature uses that step. The gate's `undefined-steps` check already runs them (Cucumber's dry run reports the step `AMBIGUOUS`);
- Reqnroll fails on an ambiguous binding when the scenario runs;
- pytest-bdd fails on nothing: the definition registered last wins, silently, and a conftest step loses to a module's own.

So `duplicate-steps` catches what none of them does: a duplicate no feature uses yet, and pytest-bdd's silent override.

**Why coverage and mutation gaps warn instead of failing.** A target line no scenario runs, or a mutant no scenario notices, means one of two things:

- a scenario claims that behavior, and its step definitions go around the real code or assert less than the Then states. The coverer has to fix that;
- no scenario claims it. That's a gap in the spec, and only a human can add scenarios.

A threshold can't tell the two apart; the coverage-quality-tester can.

**Acceptance tests alone.** Unit tests often get rewritten in a refactoring; the acceptance tests are meant to survive it, so they are measured on their own:

| Stack | acceptance-coverage | acceptance-mutation |
|---|---|---|
| .NET | `<commands.acceptanceTests> --collect:"XPlat Code Coverage;Format=cobertura"` | `dotnet stryker --solution <sln> -m "**/<target file name>" … --test-case-filter "<gate.acceptanceTestFilter>"`, default filter `FullyQualifiedName~Acceptance` |
| TypeScript | `commands.acceptanceTests` with `NODE_V8_COVERAGE`, merged by `coverage.mjs` | StrykerJS's command runner with `commands.acceptanceTests` over the targets; `skipped` with playwright-bdd, as for Next.js |
| Next.js | `commands.acceptanceTests` (server and browser coverage), merged by `coverage.mjs` | `skipped`: every mutant would need its own production build |
| Python | `python3 -m pytest <acceptanceTestsDir> --cov=<sourceDirs> --cov-branch` | mutmut over the target modules, with a pytest plugin from the gate that deselects every test outside `acceptanceTestsDir` |

Overrides: `gate.commands.acceptanceCoverage` with `gate.acceptanceCoverageReports` (Cobertura globs), and `gate.commands.acceptanceMutation` with `gate.acceptanceMutationReport` (a Stryker JSON report). A Python override must leave mutmut's results for `mutmut results`. The per-stack references have the details.

**Coverage targets** are the production files whose behavior the scenarios pin down. The acceptance-test-coverer writes them to `.atdd/<issue-key>/cover/targets.txt` (per `/atdd:cover-queue` unit, `.atdd/<issue-key>/cover/<file>/targets.txt`), and the coverage reviewer checks they're honest.

## Reading the results

| File in `<out>/` | Contents |
|---|---|
| `report.json` | `passed`, `base`, `head`, `changedFiles`, `thresholds`, and `checks[]` with `{name, status, metric, threshold, detail, log, seconds}`. `status` is `pass`, `fail`, `flaky` (tests only), `warn` or `skipped` |
| `logs/<check>.log` | full tool output, starting with the exact command |
| `build-warnings.txt` | .NET warnings in changed code |
| `dependency-cycles.json` | every cycle: `kind` (namespace or project), `members`, the shortest `cycle`, the `evidence` for each hop, and `touchedByThisChange` |
| `uncovered-lines.txt` | changed lines no test executes, per file |
| `uncovered-branches.txt` | `file:line: n of m branches taken` for changed lines with branches no test takes |
| `mutation-survivors.txt` | `file:line: mutator -> replacement [Survived/NoCoverage]`; for Python, mutmut mutant names (`mutmut show <name>`) |
| `jscpd/jscpd-report.json` | clone pairs |
| `target-coverage.json`, `target-uncovered.txt` | cover mode: per target file, lines and branches run by the acceptance tests, and the ones never run |
| `acceptance-mutation-survivors.txt` | cover mode: mutants in the targets the acceptance tests didn't notice, as `file:line: mutator -> replacement [Survived/NoCoverage]`, or for Python mutmut names, with each diff in `logs/acceptance-mutation-diffs.log` |
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
- **npath.** Name the function, its NPath and the limit. Independent decisions in sequence multiply, so point at the runs of sequential `if`s, `&&`/`||` chains and `?:`s, and suggest splitting them into named steps that each own one decision (or a lookup table instead of a chain of conditions).
- **dependency-cycles.** Quote the loop with its evidence lines. Say which dependency to reverse or which shared piece to move into a namespace both sides may depend on, following the architecture docs. A cycle the change touches is a blocker for the implementer. A cycle that was already in the code base still fails the gate: report it as a blocker addressed to a human, because breaking it is usually outside the issue's scope.
- **diff-coverage.** Group the uncovered lines by the behavior they implement, and say which test level fits. A scenario's path that isn't covered means a step definition doesn't reach it. For a branch gap (`file:line: 1 of 2 branches taken`), name the condition on that line and the case no test exercises, e.g. "no test saves a goal for a member who already has one". Code no test can reach (a defensive check, a fallback for an impossible state) should go, not get a contrived test.
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
- **A `flaky` on tests** is a minor, advisory finding, never a blocker and never the implementer's to fix: name each flaky test and quote the reason from the detail, so a human can fix it in a change of its own.
- **A `warn` on vulnerabilities**, or a `skipped` because rules aren't configured, is a minor finding addressed to a human, not to the implementer.
- **production-untouched** (cover mode). List the files. They must be reverted, or deleted if the change created them; a change outside the test paths is never the coverer's to make.
- **duplicate-steps** (cover mode). Name the step text and every definition the log lists. Say which one stays (usually the older, or the one in the shared place for steps) and that the other goes, with its callers reusing the one that stays.
- **acceptance-coverage / acceptance-mutation** (cover mode). Judge each gap rather than reporting the metric: if a scenario claims the behavior, the gap is a major for the coverer (a step that goes around the real path, or asserts less than its Then); if no scenario claims it, it's a spec gap, a minor finding for a human. Group gaps by behavior.

## Setting the gate up in a repo

`/atdd:init` does this. These references hold the exact steps and configs for each stack:

- `${CLAUDE_SKILL_DIR}/references/dotnet.md`
- `${CLAUDE_SKILL_DIR}/references/typescript.md`
- `${CLAUDE_SKILL_DIR}/references/nextjs.md`: Next.js with React, Prisma and MySQL. It builds on the TypeScript setup.
- `${CLAUDE_SKILL_DIR}/references/python.md`

The per-stack templates sit in `${CLAUDE_SKILL_DIR}/templates/<stack>/`, the QA start and stop templates in `templates/common/` (`qa-up.sh`, `qa-down.sh`; Next.js has its own), and the gate itself in `scripts/gate.py`. Next.js also uses the TypeScript stack's `coverage.mjs`, `cobertura-reporter.mjs`, `eslint.quality.mjs` and `eslint-npath.mjs`.

**Changing a threshold** is a human decision. Edit `gate.thresholds` in `.claude/atdd.json`. Keep the tools' own copies in step:

- .NET: `SonarLint.xml` (S3776) and `CodeMetricsConfig.txt` (CA1502);
- Python: ruff's `mccabe` and `pylint` settings.

The gate itself reads `gate.thresholds`, and hands `cognitive` and `npath` to `eslint.quality.mjs` for TypeScript and Next.js.

## Upgrading a repo set up before NPath, cycles and branch coverage

1. Copy `scripts/gate.py` over `scripts/atdd/gate.py`.
2. Add `"diffBranchCoverage": 100` and `"npath": 200` to `gate.thresholds`, and set `diffCoverage` to 100. Unset thresholds take these defaults anyway.
3. Add the stack's new files:
   - **.NET:** `templates/dotnet/dotnet-quality/` to `scripts/atdd/dotnet-quality/`.
   - **TypeScript:** `eslint-npath.mjs`, the new `eslint.quality.mjs`, `coverage.mjs` and `cobertura-reporter.mjs` to `scripts/atdd/`; then switch coverage from c8 to Monocart as `references/typescript.md` describes.
   - **Python:** nothing; `gate.py` computes NPath and the import graph itself.
4. Run `python3 scripts/atdd/gate.py fast --only npath,dependency-cycles` and `python3 scripts/atdd/gate.py full --only diff-coverage`. Existing dependency cycles fail every run from now on, including the implementer's commit hook: break them in a separate change before the next `/atdd:build`.

## Upgrading a repo for /atdd:cover

1. Copy `scripts/gate.py` over `scripts/atdd/gate.py`. The `/atdd:cover` preparer refuses to start until `gate.py --help` lists `cover-fast` and `cover-full`.
2. In `.claude/atdd.json`, check that `gate.testDirs` names the folders where acceptance tests may be added, and nothing else. Set `cover.testPaths` (globs) to narrow it, for example to the acceptance test project alone. Make sure `commands.acceptanceTests` is set.
3. Optionally add `"coverFastChecks": "python3 scripts/atdd/gate.py cover-fast"` and `"coverGate": "python3 scripts/atdd/gate.py cover-full"` to `commands`, and `"coverRounds": 5` to `limits`. Those are the defaults.
4. Commit it all on the base branch, then run `python3 scripts/atdd/gate.py cover-fast` once to see that nothing reports a missing tool.
