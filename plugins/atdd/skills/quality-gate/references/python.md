# Quality gate setup: Python

Templates are in `${CLAUDE_SKILL_DIR}/templates/python/`. These versions were verified end-to-end in October 2026: pytest 9, pytest-bdd 9.0, pytest-cov 7.1, ruff, complexipy 8.3, mutmut 3.8.1, vulture, pip-audit, import-linter, lizard 1.24.3, diff-cover 11 and jscpd 5.4.2.

## Prerequisites

- **Python 3.11+** for the project's tools. diff-cover 11 dropped 3.10. `gate.py` itself runs on 3.9+.
- **POSIX or WSL.** mutmut needs `fork`.
- **Node.js**, for `npx jscpd@5.4.2`, which the gate pins.

## Dev dependencies

```bash
pip install pytest pytest-bdd pytest-cov ruff complexipy mutmut vulture pip-audit import-linter lizard diff-cover
```

Put them in the repo's dev requirements or dependency group, pinned.

## Files to add or merge

| Template | Goes to | Notes |
|---|---|---|
| `atdd.json` | `.claude/atdd.json` | Set `featuresDir`, `gate.sourceDirs` and `gate.testDirs`, `gate.acceptanceTestsDir` and the commands |
| `pyproject-atdd.toml` | merge into `pyproject.toml` | ruff complexity rules, pytest `testpaths`/`norecursedirs` (keeps `mutants/` out of collection), `bdd_features_base_dir`, mutmut, vulture. Uncomment and fill in `[tool.importlinter]` when the layers are known |
| `${CLAUDE_SKILL_DIR}/scripts/gate.py` | `scripts/atdd/gate.py` | The gate itself. Copy it unchanged |
| `../common/qa-up.sh`, `../common/qa-down.sh` | `scripts/atdd/` | Adapt `qa-up.sh` (e.g. `uvicorn myapp.main:app --port 8000`) |

Add `mutants/`, `.coverage`, `.atdd/` and `.pytest_cache/` to `.gitignore`.

The acceptance tests must bind every scenario with `pytest_bdd.scenarios(...)`. With `bdd_features_base_dir = "features/"`, that's `scenarios(".")` (see the gherkin-binding skill). The gate's undefined-steps check requires a `scenarios(` call under `gate.acceptanceTestsDir`. A missing step then fails the test run with `StepDefinitionNotFoundError`.

## How the gate drives the tools

- **Build:** `python3 -m compileall -q <sourceDirs>`. Override with a type checker (`mypy`, `pyright`) via `gate.commands.build` if the repo uses one.
- **Format:** `ruff format --check <changed files>`.
- **Lint:** `ruff check --output-format json`. Only problems on added lines count.
- **Cognitive:** `complexipy <changed files> --max-complexity-allowed <cognitive> --output-format json`. Results map to functions with lizard, and only changed functions count.
- **Tests (full mode):** `python3 -m pytest --ignore=mutants --cov=<sourceDirs> --cov-branch --cov-report=xml:<out>/coverage.xml`.
- **Mutation:** `mutmut run "<module>.*"` for every changed production module, then `mutmut export-cicd-stats`. The score is (killed + timeout) / (total − skipped). `mutmut run` exits 0 even with survivors, so the gate reads the stats file.
  - Unlike .NET and TypeScript, the scope is the whole changed module. mutmut has no line filter.
  - mutmut 3.7+ only re-tests functions whose source changed, so later rounds are fast.
- **Architecture:** `lint-imports`. Skipped until `[tool.importlinter]` exists.
- **Dead code:** `vulture <sourceDirs> <testDirs> --min-confidence 60`. Scanning the tests too means test usages count. Only items on added lines in production code are reported. Put genuine dynamic uses in `vulture_whitelist.py` (a locked file, so a human adds them).
- **Vulnerabilities:** `pip-audit` (the active environment). Override with `pip-audit -r requirements.txt` or `pip-audit .` via `gate.commands.vulnerabilities`.
