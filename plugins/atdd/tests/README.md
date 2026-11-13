# Tests for the workflows

Four kinds, besides the eval cases in `../evals/`:

- **`guard/`**: `scripts/atdd_guard.py` fed synthetic PreToolUse payloads in a throwaway repo: who may edit, commit, push, rebase and open pull requests, and which foreground waits QA may run. `python3 tests/guard/guard_test.py`

- **`gate/`**: the quality gate's command line, in fast and full mode, against a throwaway Python repo built by `fixture.py` from the eval app below; asserts on `report.json`. No model calls; needs the Python stack's tools (see `skills/quality-gate/references/python.md`) and Node.js. `PATH="<venv>/bin:$PATH" python3 tests/gate/gate_test.py`

  `dotnet_gate_test.py` does the same against a throwaway .NET solution built by `dotnet_fixture.py` (xUnit, Reqnroll, coverlet; Stryker.NET 5.0.0 as a local tool for the full-mode test). Needs the .NET 10 SDK and diff-cover, and the NuGet packages in the local cache or reachable. `python3 tests/gate/dotnet_gate_test.py`

- **`workflows/`**: the workflow scripts' control flow, with `agent()` and `workflow()` mocked. Fast, no model calls.

  ```bash
  node tests/workflows/check.mjs workflows/*.js
  node tests/workflows/queue-test.mjs workflows/cover-queue.js
  node tests/workflows/build-test.mjs workflows/build.js
  node tests/workflows/cover-test.mjs workflows/cover.js
  ```

- **`cover-queue-e2e/`**: a real `/atdd:cover-queue` run on a throwaway fixture, built on the Python app of the `cover-reports-discrepancy-not-code-change` eval. Its `inventory` branch holds two areas of two `.feature` files each, a Given step both areas use, and one scenario the code contradicts (a goal of exactly 50,000 steps). It has a bare `origin` and a stub `gh` that records its calls.

  ```bash
  F=/tmp/cover-queue-e2e; tests/cover-queue-e2e/build.sh "$PWD" $F
  cd $F/repo && PATH="$F/bin:<venv with pytest, pytest-bdd, pytest-cov, ruff, lizard, complexipy, mutmut>/bin:$PATH" GH_STUB_LOG=$F/gh-calls.jsonl \
    claude -p "/atdd:cover-queue inventory a01-goals a02-celebrations --open-prs" --plugin-dir <this plugin> \
    --permission-mode dontAsk --allowedTools "Workflow(atdd:cover-queue)" "Workflow(atdd:cover)" Read Write Edit Glob Grep "Bash(git *)" "Bash(python3 *)" "Bash(gh *)" "Bash(npx *)"
  python3 <this plugin>/tests/cover-queue-e2e/verify.py $F --snapshot $F/after-first.json
  # run the same claude -p command again, then:
  python3 <this plugin>/tests/cover-queue-e2e/verify.py $F --unchanged-since $F/after-first.json
  ```

  Expected: three units done, with PRs stacked on each other (the first on `atdd/cover-base`); `a01-goals/goal-range.feature` parked with its test files in a WIP commit; the shared Given defined once; no unit branch changing a file outside `tests/` and `features/`; and the second run changing nothing.
