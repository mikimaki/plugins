# atdd: a Gherkin-first ATDD pipeline for Claude Code

Turns a spec from your issue tracker into reviewed Gherkin, then into an implementation. The implementation must survive a code reviewer, a manual QA pass and a brutal deterministic quality gate. Each failed check sends the work back to the implementer.

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
                                      → reviewer → qa → quality-tester    (fail-fast, ≤ 5 rounds)
                                      any blocker/major finding → implementer, then restart at reviewer
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

   Commit what it adds.
2. **Write the spec** with Matt Pocock's skills (`/grill-with-docs`, `/to-spec`). The spec lands in your issue tracker.
3. **Run `/atdd:gherkin PROJ-45`.** Accepted references:
   - a tracker key or `#123`;
   - a URL;
   - a spec file path.

   You get an approved ledger and scenarios in `<featuresDir>/<key>/`, uncommitted, on branch `atdd/<key>`. Read them. Edit anything you disagree with. If the ledger lists open questions, answer them in the spec and re-run.
4. **Run `/atdd:build PROJ-45`.** It ends either `done` on a commit that passed every check, or `needs-human` with the outstanding findings. Review the diff, push, and open the PR yourself.

**Run it in a dedicated worktree** (`claude --worktree`, or `git worktree add`). The pipeline needs a clean working tree and commits on the branch.

**Pre-approve the routine commands.** The workflow's agents run many shell commands, and in Manual mode each one is a permission prompt. Either run in auto mode, or add allow rules to the repo's `.claude/settings.local.json`. The hooks still enforce the pipeline's limits whatever you allow. Add the stack's tools (`Bash(dotnet *)`, `Bash(npx *)`/`Bash(npm *)`, `Bash(pytest *)`, `Bash(curl *)`, `Bash(hurl *)`) as needed:

```json
{
  "permissions": {
    "allow": [
      "Workflow(atdd:gherkin)", "Workflow(atdd:build)",
      "Bash(git *)", "Bash(python3 *)", "Bash(bash scripts/atdd/*)",
      "Bash(mkdir *)", "Bash(ls *)", "Bash(cat *)", "Bash(cp *)", "Bash(echo *)"
    ]
  }
}
```

**Running headless:** in `claude -p`, allow the workflow and the tools the agents need. For example:

```bash
claude -p "/atdd:build PROJ-45" --permission-mode dontAsk --allowedTools "Workflow(atdd:build)" Read Write Edit Glob Grep "Bash(git *)" "Bash(python3 *)" "Bash(dotnet *)"
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
| `preparer` | Workflow housekeeping: config, spec snapshot, branch, commits |

**Skills** (`skills/`):

| Skill | Contents |
|---|---|
| `gherkin-style` | The house rules for scenarios, plus `scripts/gherkin_check.py`, a deterministic coverage and structure checker |
| `gherkin-binding` | How to bind scenarios honestly in .NET (Reqnroll), TypeScript (Cucumber.js/playwright-bdd) and Python (pytest-bdd) |
| `coding-standards` | A copy of your non-negotiable clean-code standards plus the C# rules from your Cowork skills, with domain-neutral examples. Keep the rules in sync, or delete one copy |
| `architecture-review` | How to judge whether modules and dependencies fit the codebase |
| `qa-playbook` | The manual QA method and the evidence format |
| `quality-gate` | The gate script, `scripts/gate.py` (`/atdd:init` copies it into each repo as `scripts/atdd/gate.py`), plus what each check measures, how to read failures, per-stack setup and templates |
| `init` | `/atdd:init` |

**Workflows:** `workflows/gherkin.js` (`/atdd:gherkin`) and `workflows/build.js` (`/atdd:build`).

**Hooks** (`hooks/hooks.json` → `scripts/atdd_guard.py`). They enforce only for atdd agents in repos that have `.claude/atdd.json`; your normal sessions are untouched.

| Agent | What's enforced |
|---|---|
| implementer | Can't edit `lockedPaths` (scenarios, ledger, gate scripts, threshold configs). Every `git commit` first checks for modified locked files, then runs `commands.fastChecks`; the commit is refused while either fails. No push, rebase, amend, hard reset or branch switching |
| gherkin-writer | Writes only inside `featuresDir` and `.atdd/` |
| reviewers, qa, quality-tester, preparer | Write only under `.atdd/` |
| read-only agents | Only read-only git commands |

## The quality gate in one paragraph

Once `/atdd:init` has copied it into a repo, `python3 scripts/atdd/gate.py fast|full` judges only the change since the base commit (clean as you code). It covers:

- build and analyzer warnings;
- format and lint;
- tests and unbound Gherkin steps;
- cyclomatic and cognitive complexity of changed functions;
- diff coverage;
- mutation score on changed lines;
- new duplication;
- architecture rules;
- unused code;
- vulnerable dependencies when manifests changed;
- new suppressions;
- locked files.

It guards against silent passes: missing tools, zero mutants, unmeasured coverage and crashed checks all fail or say why they were skipped. Details are in `skills/quality-gate/SKILL.md`.

The TypeScript and Python paths were run end-to-end against sample repos while this plugin was built. The .NET path follows current docs and tool source but hasn't run against a real solution yet. Check `.atdd/quality/latest/logs/` after the first run.

## Where things go in a repo

```text
.claude/atdd.json                    config (locked)
scripts/atdd/gate.py, qa-*.sh        gate and QA scripts, copied in by /atdd:init (locked)
<featuresDir>/<key>/requirements.md  ledger (locked once approved)
<featuresDir>/<key>/*.feature        scenarios (locked once approved)
.atdd/                               working area, git-ignored: spec snapshots, review notes, QA evidence, gate reports, logs
```

## Tests

`evals/` holds four test cases for the Gherkin stage, in the layout `claude plugin eval` reads. Each case has a fixture repo, a spec and graded expectations.

The fixtures need a concrete product to describe, so each case invents one: a fitness app, a library and a podcast player. None of their vocabulary appears in the skills, so a pass shows the rules carry over to a domain the plugin has never seen.

| Case | What it checks |
|---|---|
| `gherkin-covers-boundaries-and-time` | Boundaries at, just below and just above a goal; a value range; time-zone day edges; data that arrives after its day ends; a paused setting that keeps its value; no invented behavior |
| `gherkin-flags-contradictory-spec` | Two contradicting rules become an open question, not a guess |
| `gherkin-declarative-from-ui-spec` | A UI-worded spec becomes declarative, glossary-language steps, with the user-visible messages kept verbatim |
| `gherkin-review-catches-seeded-defects` | The reviewer catches seven planted defects, with precise findings |

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

**Rerunning:** `claude plugin eval ~/personal-skills/plugins/atdd --scaffold --allow-tools Workflow,Read,Write,Edit,Glob,Grep,Bash` reruns the cases. That CLI compares runs with and without the plugin using the same prompt, and three of the prompts are `/atdd:gherkin …`, which only exists with the plugin. So use it as a regression check, not as a comparison.
