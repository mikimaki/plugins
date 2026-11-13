# atdd: a Gherkin-first ATDD pipeline for Claude Code

Turns a spec from your issue tracker into reviewed Gherkin, then into an implementation. The implementation must survive a code reviewer, a brutal deterministic quality gate and a manual QA pass. Each failed check sends the work back to the implementer.

For behavior the code already has, `/atdd:cover` turns the same reviewed Gherkin into acceptance tests instead, and changes nothing else. That's the way into a platform that has no acceptance tests yet: cover what it does first, then refactor with the tests as a safety net. `/atdd:cover-queue` does that for a whole inventory of Gherkin, unattended, one `.feature` file and one PR at a time.

```text
Matt Pocock's skills (you)            this plugin
/grill-with-docs → /to-spec   ──►  /atdd:gherkin <issue>
  spec in the tracker named in        preparer      snapshot the spec, branch atdd/<key>
  docs/agents/issue-tracker.md        gherkin-writer ⇄ gherkin-reviewer   (≤ 3 rounds)
                                      → <featuresDir>/<key>/requirements.md + *.feature

                                    you read the scenarios (and edit them if you like)

                                   /atdd:build <issue>
                                      preparer      commit the scenarios, record the base
                                      implementer   step definitions + code → commit (commit hook runs the fast gate)
                                      → reviewer → quality-tester → qa    (fail-fast, ≤ 5 rounds)
                                      any blocker/major finding → implementer, then restart at reviewer

                                   or, when the code already does what the scenarios say:

                                   /atdd:cover <issue>
                                      preparer      commit the scenarios, check the branch changes only tests
                                      acceptance-test-coverer   step definitions against the existing code → commit
                                                    (commit hook: only test paths changed, then the cover fast gate)
                                      → coverage-reviewer → coverage-quality-tester   (≤ 5 rounds)
                                      any blocker/major finding → coverer; a scenario the code contradicts → you

                                   or, for many areas of Gherkin already on a branch:

                                   /atdd:cover-queue <source-ref> <area> …
                                      one /atdd:cover run per .feature file, each on its own branch stacked
                                      on the last one that passed → a PR per file (opt-in); failed files parked
```

## Install

This plugin lives in the `personal-skills` marketplace (the folder above this one).

```text
/plugin marketplace add ~/personal-skills
/plugin install atdd@personal-skills
```

To try changes without installing, run `claude --plugin-dir ~/personal-skills/plugins/atdd`.

**Requirements:**
- Claude Code with dynamic workflows. This plugin was tested with v2.1.312.
- `python3` on the PATH. The hooks and the gate are Python.
- Per-stack tools, which `/atdd:init` installs or lists.

## Use it

1. **Once per repo**, run `/atdd:init`. It writes:
   - `.claude/atdd.json`;
   - `scripts/atdd/gate.py` and the stack's tool configs;
   - QA start/stop scripts;
   - the strict Gherkin runner config.

   Commit what it adds. If it lists existing dependency cycles, break them in a change of their own first: every gate run fails while there are any.
2. **Write the spec** with Matt Pocock's skills (`/grill-with-docs`, `/to-spec`). The spec lands in your issue tracker.
3. **Run `/atdd:gherkin PROJ-45`.** Accepted references:
   - a tracker key or `#123`;
   - a URL;
   - a spec file path.

   You get an approved ledger and scenarios in `<featuresDir>/<key>/`, uncommitted, on branch `atdd/<key>`. Read them. Edit anything you disagree with. If the ledger lists open questions, answer them in the spec and re-run.
4. **Run `/atdd:build PROJ-45`.** It ends either `done` on a commit that passed every check, or `needs-human` with the outstanding findings. Review the diff, push, and open the PR yourself.

   **Or, if the code already does what the scenarios describe, run `/atdd:cover PROJ-45`.** It binds the scenarios to the existing code and changes nothing outside the test paths (`cover.testPaths`, else `gate.testDirs` plus `featuresDir`). A hook keeps the coverer's edits and commits there, and the coverage reviewer and the quality gate each check the whole branch again. There is no QA stage: nothing but tests changed, so there is no new behavior to replay by hand. The run ends in one of three ways:
   - `done`: the branch adds acceptance tests and nothing else. The advisory findings list behavior no scenario covers yet, which is worth adding to the spec before you refactor.
   - `discrepancies`: the code doesn't do what some scenarios say. It changes neither: you decide whether the scenario is wrong (fix the spec or the scenarios) or the code is (fix it in a change of its own on the base branch). Then re-run; the coverer picks up its uncommitted test files.
   - `needs-human`: something only you can change, such as a seam the code lacks (it reads the clock directly, say) or a missing test package. Make that change on the base branch, update this branch from it, and re-run.

5. **Or cover a whole inventory with `/atdd:cover-queue`** (next section).

**Run it in a dedicated worktree** (`claude --worktree`, or `git worktree add`). The pipeline needs a clean working tree and commits on the branch.

## Covering many feature files: `/atdd:cover-queue`

When reviewed Gherkin for many areas already exists on some branch (an as-built inventory, say), `/atdd:cover-queue` covers it one `.feature` file at a time, sequentially and unattended. Each file is a **unit**. A unit that passes every check gets a PR, and the queue moves on without waiting for review.

```text
/atdd:cover-queue <source-ref> <area> [<area> …] [<area>/<file>.feature …] [--max-units N] [--open-prs] [--resume-parked]
```

- `<source-ref>`: the branch (or commit) holding `<featuresDir>/<area>/requirements.md`, the `.feature` files and the specs the ledgers name on their `Source:` line. The queue only reads it.
- Areas run in the order given, and the files of an area in file-name order. Name `<area>/<file>.feature` to run single units.
- `--max-units N` stops after N units have run; `--open-prs` pushes and opens PRs (or set `cover.openPrs`).

**How it works:**

```text
origin/<baseBranch> ── atdd/cover-base ── unit 1 ── unit 3 ── unit 4      (each PR targets the branch before it)
                                              └── unit 2 (parked: WIP commit, no PR)
```

- **Integration branch.** `cover.integrationBranch` (default `atdd/cover-base`), created from `origin/<baseBranch>` when it's missing. It collects the reviewed units; your final PR takes it into the base branch.
- **A stack of unit branches.** Unit `atdd/cover-<area>-<file>` is cut from the last unit that ended `done`, the first from the integration branch. So every unit sees every earlier unit's step definitions and reuses them instead of defining them again, and each PR, opened against its parent branch, shows exactly one unit's change. The gate judges each unit against its parent too.
- **Per unit:** the preparer brings in the area's ledger and the one `.feature` file from the source ref (`git checkout <ref> -- <paths>`) and commits them; the spec is snapshotted into `.atdd/<area>/spec.md` and never enters the branch. Then the full `/atdd:cover` loop runs: coverer → coverage reviewer → quality gate.
- **Duplicate steps can't slip through.** The coverer searches the existing step definitions first and keeps shared steps in one place (`features/steps/common/`, the acceptance tests' `conftest.py`, a shared binding class). The gate's `duplicate-steps` check fails on a step text defined twice, and the reviewer flags it.
- **Park.** A unit that ends in discrepancies, needs a human for a reason of its own (a missing seam, a question the spec doesn't answer) or runs out of rounds is parked: its test files go into a WIP commit on its own branch, the reason is recorded, and the queue carries on from the last `done` unit with a clean tree. No work is lost.
- **Stop.** A problem every unit would hit stops the whole queue with a reason: the gate is too old or the coverage setup is missing, the app doesn't start, a tool is missing, the tree isn't clean. So does `cover.maxConsecutiveParks` (default 3) units parked in a row.
- **Publish** (opt-in). After `done`, the queue pushes the unit branch and runs `gh pr create --base <parent> --head <unit>`, with the scenarios covered, the acceptance coverage and the advisory spec gaps in the body. It pushes only branches it created, never the base or integration branch, and never by force. A failed push or PR is recorded and the queue carries on.
- **Progress lives in git.** Each unit branch records its parent and verdict in `git config` (`branch.<unit>.atddBase`, `atddStatus`, `atddReason`, `atddPr`), and a unit whose `.feature` file is on the integration branch counts as done. Re-running the same command skips done units, resumes a unit a stopped run left half done, and leaves parked units alone.
- **The report** lists, per unit, its status, branch, parent, PR URL and parked reason, and per area, once all of its units have run, the ledger's requirements no done unit covers.

**One-time setup:**
- `/atdd:init`'s gate must be 0.4.0 or later (`python3 scripts/atdd/gate.py --version`). If it isn't, copy `skills/quality-gate/scripts/gate.py` over `scripts/atdd/gate.py` and commit it **on the base branch**: committed on a unit branch it would fail `production-untouched`.
- With `--open-prs`, `gh` must be signed in, and the integration branch must exist on `origin`, because the first PR targets it and the queue never pushes it. Create it once: `git push origin origin/<baseBranch>:refs/heads/atdd/cover-base`.

**Reviewing and merging the stack:**
1. Review the PRs from the bottom: the first targets the integration branch, each later one the unit before it.
2. Merge each into its base with a **merge commit** (not squash or rebase, which would rewrite the commits the next PR is built on). Delete the merged branch; GitHub retargets the next PR to the integration branch.
3. When all are in, open one PR from the integration branch into the base branch.

**When a review asks for changes** on a unit in the middle of the stack:
1. Check out its branch, change the tests (by hand, or in a session that reads the review), commit and push.
2. Bring the change up the stack, from that unit to the top: `git switch <next unit>`, `git merge <previous unit>`, `git push`, for each later unit in order. Merging keeps every pushed branch's history, so nothing needs a force-push.
3. Re-run the gate on the top unit (`python3 scripts/atdd/gate.py cover-full`) to check the stack still holds.

If you'd rather rebase the stack (`git rebase --update-refs`), you'll have to force-push the branches yourself; the queue never does.

**Resuming a parked unit:**
1. Read its reason in the report (or `git config branch.<unit>.atddReason`).
2. Fix the cause: a wrong scenario on the source ref, a bug in a change of its own on the base branch, or a seam or package on the base branch.
3. Run `/atdd:cover-queue <source-ref> <area>/<file>.feature --resume-parked` (or `--resume-parked` with whole areas). The preparer rebases the parked branch onto the current top of the stack (it was never pushed, so that's safe), and the coverer continues from its committed test files. Once it passes, it's the new top of the stack and its PR targets the unit below it.

A unit whose scenarios fail the Gherkin check is reported as `invalid scenarios` and skipped; fix them on the source ref.

**Pre-approve the routine commands.** The workflow's agents run many shell commands, and in Manual mode each one is a permission prompt. Either run in auto mode, or add allow rules to the repo's `.claude/settings.local.json`. The hooks still enforce the pipeline's limits whatever you allow. Add the stack's tools (`Bash(dotnet *)`, `Bash(npx *)`/`Bash(npm *)`, `Bash(pytest *)`, `Bash(curl *)`, `Bash(hurl *)`) as needed:

```json
{
  "permissions": {
    "allow": [
      "Workflow(atdd:gherkin)", "Workflow(atdd:build)", "Workflow(atdd:cover)", "Workflow(atdd:cover-queue)",
      "Bash(git *)", "Bash(python3 *)", "Bash(bash scripts/atdd/*)", "Bash(gh *)",
      "Bash(mkdir *)", "Bash(ls *)", "Bash(cat *)", "Bash(cp *)", "Bash(echo *)"
    ]
  }
}
```

**Running headless:** in `claude -p`, allow the workflow and the tools the agents need. For example:

```bash
claude -p "/atdd:build PROJ-45" --permission-mode dontAsk --allowedTools "Workflow(atdd:build)" Read Write Edit Glob Grep "Bash(git *)" "Bash(python3 *)" "Bash(dotnet *)"
```

The queue also needs `/atdd:cover`, which it runs per unit, and `gh` when it opens PRs:

```bash
claude -p "/atdd:cover-queue claude/inventory a01-accounts a03-catalogue --max-units 5 --open-prs" --permission-mode dontAsk --allowedTools "Workflow(atdd:cover-queue)" "Workflow(atdd:cover)" Read Write Edit Glob Grep "Bash(git *)" "Bash(python3 *)" "Bash(gh *)" "Bash(npx *)"
```

## What's inside

**Agents** (`agents/`):

| Agent | Role |
|---|---|
| `gherkin-writer` | Builds the requirements ledger and declarative scenarios. Records open questions instead of inventing behavior |
| `gherkin-reviewer` | Forms its own reading of the spec first, then checks faithfulness, coverage, testability and style. Severity-ranked findings |
| `implementer` | Binds the scenarios (red), implements (green), refactors, commits, and fixes findings |
| `reviewer` | Read-only. Architecture and ADR fit, module boundaries, coding standards, honest step definitions |
| `qa` | Starts the app, replays every scenario by hand (Hurl/curl or the Playwright MCP browser), explores around the change, files reproducible findings |
| `quality-tester` | Runs `gate.py full` and turns every failed check into an actionable finding |
| `acceptance-test-coverer` | `/atdd:cover`: binds the scenarios to existing code without changing it, reports discrepancies between a scenario and the code instead of resolving them, commits, and fixes findings in the tests |
| `coverage-reviewer` | Read-only. Judges only the tests: nothing outside the test paths changed, honest step definitions, a safety net worth trusting, the coverage targets. Never asks for production changes |
| `coverage-quality-tester` | Runs `gate.py cover-full`, and decides for every coverage gap and surviving mutant whether the coverer must fix it or it's a spec gap for you |
| `preparer` | Workflow housekeeping: config, spec snapshot, branch, commits; for `/atdd:cover-queue` also the plan, parking and publishing |

**Skills** (`skills/`):

| Skill | Contents |
|---|---|
| `gherkin-style` | The house rules for scenarios, plus `scripts/gherkin_check.py`, a deterministic coverage and structure checker |
| `gherkin-binding` | How to bind scenarios honestly in .NET (Reqnroll), TypeScript (Cucumber.js/playwright-bdd), Next.js (playwright-bdd against the production build, Vitest and React Testing Library for units) and Python (pytest-bdd), plus `references/existing-code.md` for binding code that may not change |
| `acceptance-test-review` | How to judge tests added to existing code: nothing else changed, honest steps, usefulness as a safety net for refactoring, coverage targets, test-code quality. The coverage reviewer's standard |
| `coding-standards` | A copy of your non-negotiable clean-code standards plus the C# rules from your Cowork skills, with domain-neutral examples, and TypeScript/React rules that exist only here. Keep the rules in sync, or delete one copy |
| `architecture-review` | How to judge whether modules and dependencies fit the codebase: zero dependency cycles, and default layering for Next.js App Router apps |
| `qa-playbook` | The manual QA method and the evidence format, with Next.js specifics (server actions, hydration errors, stale data) |
| `quality-gate` | The gate script, `scripts/gate.py` (`/atdd:init` copies it into each repo as `scripts/atdd/gate.py`), plus what each check measures, how to read failures, per-stack setup and templates. Its cover modes judge test-only changes |
| `init` | `/atdd:init` |

**Workflows:** `workflows/gherkin.js` (`/atdd:gherkin`), `workflows/build.js` (`/atdd:build`), `workflows/cover.js` (`/atdd:cover`, and one unit of the queue) and `workflows/cover-queue.js` (`/atdd:cover-queue`, which runs `cover.js` per unit as a nested workflow).

**Scripts:** `scripts/atdd_guard.py` (the hooks, below) and `scripts/cover_queue.py`, the queue's read-only helper: it lists the units on the source ref, derives each one's status and the top of the stack from git, checks what would stop every unit, and snapshots specs from the source ref.

**Hooks** (`hooks/hooks.json` → `scripts/atdd_guard.py`). They enforce only for atdd agents in repos that have `.claude/atdd.json`; your normal sessions are untouched.

| Agent | What's enforced |
|---|---|
| implementer | Can't edit `lockedPaths` (scenarios, ledger, gate scripts, threshold configs). Every `git commit` first checks for modified locked files, then runs `commands.fastChecks`; the commit is refused while either fails. No push, rebase, amend, hard reset or branch switching |
| acceptance-test-coverer | Writes only inside the test paths and `.atdd/`, never to `lockedPaths`. Every `git commit` first checks for modified locked files and for modified or new files outside the test paths, then runs `commands.coverFastChecks` (default `gate.py cover-fast`). The same git limits as the implementer |
| gherkin-writer | Writes only inside `featuresDir` and `.atdd/` |
| reviewers, qa, quality-tester, coverage agents, preparer | Write only under `.atdd/` |
| preparer | Commits only the feature directory and, when parking a unit, the test paths. Creates branches, brings files in from a ref only into `featuresDir`, sets only `branch.*.atdd*` git config. Pushes only unit branches the queue created (`branch.<name>.atddBase` set), never the base or integration branch, never with force, deletes or tags. Rebases only a unit branch that was never pushed |
| read-only agents | Only read-only git commands |
| every atdd agent | Only read-only `gh` commands; the preparer may also `gh pr create` for a unit branch |

## The quality gate in one paragraph

Once `/atdd:init` has copied it into a repo, `python3 scripts/atdd/gate.py fast|full` judges the change since the base commit (clean as you code). It supports .NET, TypeScript, Next.js (React, Prisma, MySQL) and Python. It covers:

- build and analyzer warnings;
- format and lint;
- tests and unbound Gherkin steps;
- cyclomatic, cognitive and NPath complexity of changed functions;
- dependency cycles between namespaces or projects, anywhere in the code base: there must be none;
- coverage of changed lines and of their branches (100% of each by default);
- mutation score on changed lines;
- new duplication;
- architecture rules;
- unused code;
- vulnerable dependencies when manifests changed;
- new suppressions;
- locked files.

It guards against silent passes: missing tools, zero mutants, unmeasured coverage and crashed checks all fail or say why they were skipped. Details are in `skills/quality-gate/SKILL.md`.

`gate.py cover-fast|cover-full` judges `/atdd:cover`'s test-only changes instead. It fails when any file outside the test paths differs from the base or a step text has more than one step definition, measures the test code rather than production code, and skips architecture and dependency cycles. It also runs the acceptance tests on their own against the production files they protect (the coverage targets): which lines they never run, and which deliberately broken versions of the code they don't notice (mutation testing). Those gaps are judged one by one, since each is either a weak test or behavior the spec doesn't cover yet.

The TypeScript, Next.js and Python paths were run end-to-end against sample repos while this plugin was built, the Next.js one against MySQL 8 (Prisma's migration engine was replaced by a stand-in there). The .NET NPath and cycle checks ran against a sample solution on SDK 8 and 10; the rest of the .NET path follows current docs and tool source but hasn't run against a real solution with NuGet packages yet. Check `.atdd/quality/latest/logs/` after the first run.

## Where things go in a repo

```text
.claude/atdd.json                    config (locked)
scripts/atdd/gate.py, qa-*.sh        gate and QA scripts, copied in by /atdd:init (locked), with the stack's
                                     gate helpers (TypeScript/Next.js: coverage and ESLint scripts; .NET: dotnet-quality/)
<featuresDir>/<key>/requirements.md  ledger (locked once approved)
<featuresDir>/<key>/*.feature        scenarios (locked once approved)
.atdd/                               working area, git-ignored: spec snapshots, review notes, QA evidence, gate reports, logs
.atdd/<key>/cover/                   /atdd:cover: coverage targets (targets.txt), gate reports
.atdd/<key>/cover/<file>/            the same per /atdd:cover-queue unit, plus its PR body
git config branch.<unit>.atdd*       /atdd:cover-queue's records: stack parent, verdict, parked reason, PR URL
```

## Tests

`evals/` holds six test cases, in the layout `claude plugin eval` reads: four for the Gherkin stage and two for `/atdd:cover`. Each case has a fixture repo, a spec and graded expectations.

The fixtures need a concrete product to describe, so each case invents one: a fitness app, a library and a podcast player. None of their vocabulary appears in the skills, so a pass shows the rules carry over to a domain the plugin has never seen.

| Case | What it checks |
|---|---|
| `gherkin-covers-boundaries-and-time` | Boundaries at, just below and just above a goal; a value range; time-zone day edges; data that arrives after its day ends; a paused setting that keeps its value; no invented behavior |
| `gherkin-flags-contradictory-spec` | Two contradicting rules become an open question, not a guess |
| `gherkin-declarative-from-ui-spec` | A UI-worded spec becomes declarative, glossary-language steps, with the user-visible messages kept verbatim |
| `gherkin-review-catches-seeded-defects` | The reviewer catches seven planted defects, with precise findings |
| `cover-review-flags-functionality-change` | The coverage reviewer refuses a "tests only" branch that rewrote a production message to make a scenario pass and edited a unit test to match. It also catches the hollow step, the faked celebration service, the read of private state and the sleep, and asks for no production refactoring |
| `cover-reports-discrepancy-not-code-change` | The coverer binds every scenario to existing code it may not change. Two scenarios disagree with the code: one on wording, one on an off-by-one bug. It reports both for a human to decide, without touching the code or the existing tests, loosening a step or committing, and fakes the push gateway at the network boundary |

**Last run (2026-10-28):**

| | Assertions passed | Time | Tokens |
|---|---|---|---|
| With the plugin | 39/40 (97.5%) | about 2.7x | about 3.7x |
| Plain Claude, same specs | 33/40 (82.5%) | 1x | 1x |

Plain Claude's misses:
- It decided a case the spec leaves open, and kept open questions the spec already answers.
- It listed both sides of a contradiction as requirements.
- It left UI wording ("the player bar") in its steps.
- Its review didn't tie a wrong actor name to the glossary.

The plugin's one miss: its review called a question open when a rule in the spec settles it.

**`/atdd:cover` cases, last run (2026-11-02):**

| | Review case | Coverer case | Coverer time | Coverer tokens |
|---|---|---|---|---|
| With the plugin | 9/9 | 12/12 | 637 s | 179k |
| Without the cover changes | 8/9 | 12/12 | 742 s | 208k |

- **The baseline's one miss:** it asked for pytest markers to be registered, a `pyproject.toml` change on a branch that may only change tests.
- **The baseline is strong here.** Told plainly not to change functionality, the model kept the code as it was, even with a discrepancy that looks like a plain bug. In the plugin, the hooks and `gate.py cover-*` enforce that rule mechanically, whatever an agent decides. These single-agent cases don't exercise those.
- **The plugin's coverer also writes the coverage targets,** and reports discrepancies in the structure the workflow expects.
- **The baseline isn't plugin-free.** It ran where the master version of this plugin was installed, and one baseline run loaded its `gherkin-binding` skill.
- **Iterations:** the coverer case's numbers come from the second iteration, after the coverer's instructions stopped it repeating the quality stage's mutation testing. The first iteration took 969 s and 278k tokens with the plugin. Each configuration ran once.
- **A caveat:** during these runs, two examples in the plugin's instructions quoted the fixtures' own wording. They've since been moved to an unrelated domain. The baselines never saw that text and passed the same assertions, so the conclusions hold. Still, rerun the cases before quoting the numbers. The review case has had a tenth assertion since then.
- **Fixtures:** both cover cases copy `skills/quality-gate/scripts/gate.py` into the fixture repo, so they always test the current gate. Their `.gitignore` ships as `dot-gitignore`, so this repo doesn't ignore the fixtures' `.atdd/` files.

**`/atdd:cover-queue`** has tests of its own in `tests/` (see `tests/README.md`): mocked runs of the workflows' control flow (stacking, parking, the stop rules, resuming, the fallback to the script path), and an end-to-end run on a throwaway fixture with two areas of two `.feature` files, a Given both areas share, a scenario the code contradicts, a bare `origin` and a stub `gh`, plus `verify.py` to check the result.

**Rerunning:** `claude plugin eval ~/personal-skills/plugins/atdd --scaffold --allow-tools Workflow,Read,Write,Edit,Glob,Grep,Bash` reruns the cases. That CLI compares runs with and without the plugin using the same prompt, and three of the prompts are `/atdd:gherkin …`, which only exists with the plugin. So use it as a regression check, not as a comparison.
