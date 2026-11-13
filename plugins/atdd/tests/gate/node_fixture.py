"""Throwaway TypeScript and Next.js repos for the quality gate: `main` holds a small goal-tracking module with a Vitest
test and one approved scenario (Cucumber.js for TypeScript, playwright-bdd for Next.js); `feature` adds
GoalBook.clearGoal with its Vitest test. The gate runs only its tests, diff-coverage and mutation checks here.

The npm packages are installed once into a shared folder (ATDD_GATE_NODE_MODULES, or one under the temp directory)
that every repo links as node_modules.

    repo = build(target, "typescript")    # or "nextjs"; target is created or replaced
    report = run_gate(repo, "fast")       # report.json as a dict
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from fixture import GATE, add_to_base, add_to_feature, commit, git, write  # noqa: F401  (re-exported)

PLUGIN = Path(__file__).resolve().parents[2]
TEMPLATES = PLUGIN / "skills" / "quality-gate" / "templates"

PACKAGES = [
    "typescript@6.0.3", "tsx@4.23.15", "vitest@5.1.0", "@vitest/coverage-v8@5.1.0", "monocart-coverage-reports@3.2.1",
    "vitest-monocart-coverage@5.0.0", "@cucumber/cucumber@13.3.0", "dependency-cruiser@18.7.2",
    "@stryker-mutator/core@10.1.1", "@stryker-mutator/vitest-runner@10.1.1", "@playwright/test@1.65.0",
    "playwright-bdd@9.2.1",
]

TSCONFIG = """{
  "compilerOptions": {
    "target": "ES2022", "module": "NodeNext", "moduleResolution": "NodeNext", "strict": true, "noEmit": true,
    "allowImportingTsExtensions": true, "skipLibCheck": true
  },
  "include": ["src", "test", "features"]
}
"""

GITIGNORE = "node_modules\n.atdd/\nreports/\ncoverage/\n.stryker-tmp/\n.features-gen/\ntest-results/\nplaywright-report/\n"

VITEST_CONFIG = """import { defineConfig } from 'vitest/config'
import { coverageDir, unitCoverageOptions } from './scripts/atdd/coverage.mjs'

export default defineConfig({
  test: {
    include: ['test/**/*.test.ts'],
    coverage: {
      enabled: coverageDir !== null,
      provider: 'custom',
      customProviderModule: 'vitest-monocart-coverage',
      ...(coverageDir && { coverageReportOptions: unitCoverageOptions() }),
    },
  },
})
"""

STRYKER_CONFIG = {
    "testRunner": "vitest",
    "vitest": {"configFile": "vitest.config.mts"},
    "coverageAnalysis": "perTest",
    "mutate": ["src/**/*.ts"],
    "ignorePatterns": [".features-gen", "reports", "coverage", ".atdd", "test-results"],
    "thresholds": {"high": 90, "low": 80, "break": None},
    "reporters": ["clear-text", "json"],
    "jsonReporter": {"fileName": "reports/mutation/mutation.json"},
}

PLAYWRIGHT_CONFIG = """import { defineConfig } from '@playwright/test'
import { defineBddConfig } from 'playwright-bdd'

const testDir = defineBddConfig({
  features: 'features/**/*.feature',
  steps: ['features/steps/**/*.ts', 'features/support/**/*.ts'],
  missingSteps: 'fail-on-gen',
})

export default defineConfig({ testDir, retries: 0, reporter: [['list']] })
"""

BDD_FIXTURES = """import { createBdd, test } from 'playwright-bdd'

export const { Given, When, Then } = createBdd(test)
"""

GOALS = """export class GoalBook {
  private readonly goals = new Map<string, number>()

  setGoal(member: string, text: string): void {
    this.goals.set(member, Number(text.replaceAll(',', '')))
  }

  goalOf(member: string): number | undefined {
    return this.goals.get(member)
  }
"""

CLEAR_GOAL = """
  clearGoal(member: string): void {
    this.goals.delete(member)
  }
"""

APP = """import { GoalBook } from './goals.ts'

export class App {
  private readonly book = new GoalBook()

  getGoal(member: string): { steps: number | undefined } {
    return { steps: this.book.goalOf(member) }
  }
}
"""

PUSH = "export const PUSH_URL = 'https://push.example.com'\n"

GOALS_TEST = """import { expect, it } from 'vitest'
import { GoalBook } from '../src/goals.ts'

it('saves a goal in range', () => {
  const book = new GoalBook()
  book.setGoal('robin', '8,000')
  expect(book.goalOf('robin')).toBe(8000)
})
"""

CLEAR_GOAL_TEST = """import { expect, it } from 'vitest'
import { GoalBook } from '../src/goals.ts'

it('forgets a cleared goal', () => {
  const book = new GoalBook()
  book.setGoal('robin', '8,000')
  book.clearGoal('robin')
  expect(book.goalOf('robin')).toBeUndefined()
})
"""

FEATURE = """Feature: Daily step goal

  Scenario: A goal in range is saved
    Given Robin has no goal
    When Robin sets a goal of "8,000" steps
    Then Robin's goal is 8000 steps
"""

CUCUMBER_STEPS = """import assert from 'node:assert/strict'
import { Given, Then, When } from '@cucumber/cucumber'
import { GoalBook } from '../../src/goals.ts'

let book = new GoalBook()

Given('Robin has no goal', () => {
  book = new GoalBook()
})

When('Robin sets a goal of {string} steps', (text: string) => {
  book.setGoal('robin', text)
})

Then('Robin\\'s goal is {int} steps', (steps: number) => {
  assert.equal(book.goalOf('robin'), steps)
})
"""

BDD_STEPS = """import { expect } from '@playwright/test'
import { GoalBook } from '../../src/goals.ts'
import { Given, Then, When } from '../support/fixtures.ts'

let book = new GoalBook()

Given('Robin has no goal', async () => {
  book = new GoalBook()
})

When('Robin sets a goal of {string} steps', async ({}, text: string) => {
  book.setGoal('robin', text)
})

Then('Robin\\'s goal is {int} steps', async ({}, steps: number) => {
  expect(book.goalOf('robin')).toBe(steps)
})
"""

COMMANDS = {
    "typescript": {"unitTests": "npx vitest run", "acceptanceTests": "npx cucumber-js"},
    "nextjs": {"unitTests": "npx vitest run", "acceptanceTests": "npx bddgen && npx playwright test"},
}


def node_modules() -> Path:
    """The shared node_modules, installed on first use."""
    folder = Path(os.environ.get("ATDD_GATE_NODE_MODULES") or Path(tempfile.gettempdir()) / "atdd-gate-node")
    if not (folder / "node_modules" / ".bin" / "depcruise").exists():
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "package.json").write_text('{"name": "atdd-gate-node", "private": true}\n', encoding="utf-8")
        subprocess.run(["npm", "install", "--no-audit", "--no-fund", "--save-dev", *PACKAGES], cwd=folder, check=True,
                       capture_output=True)
    return folder / "node_modules"


def atdd_config(stack: str) -> dict:
    return {
        "stack": stack,
        "baseBranch": "main",
        "featuresDir": "features",
        "lockedPaths": [],
        "commands": COMMANDS[stack],
        "gate": {
            "sourceDirs": ["src"],
            "testDirs": ["test", "features"],
            "acceptanceRunner": "cucumber" if stack == "typescript" else "playwright-bdd",
            "mutationReport": "reports/mutation/mutation.json",
            "fast": ["tests"],
            "full": ["tests", "diff-coverage", "mutation"],
        },
    }


def build(target: Path, stack: str) -> Path:
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    (target / "node_modules").symlink_to(node_modules(), target_is_directory=True)
    git(target, "init", "-q", "-b", "main")
    git(target, "config", "user.email", "fixture@example.com")
    git(target, "config", "user.name", "fixture")
    scripts = target / "scripts" / "atdd"
    scripts.mkdir(parents=True)
    shutil.copy(GATE, scripts / "gate.py")
    for name in ("coverage.mjs", "cobertura-reporter.mjs", "tsx-register.js"):
        shutil.copy(TEMPLATES / "typescript" / name, scripts / name)
    files = {
        "package.json": '{"name": "stride", "private": true, "type": "module"}\n',
        "tsconfig.json": TSCONFIG,
        ".gitignore": GITIGNORE,
        ".claude/atdd.json": json.dumps(atdd_config(stack), indent=2) + "\n",
        "vitest.config.mts": VITEST_CONFIG,
        "stryker.config.json": json.dumps(STRYKER_CONFIG, indent=2) + "\n",
        "src/goals.ts": GOALS + "}\n",
        "src/app.ts": APP,
        "src/push.ts": PUSH,
        "test/goals.test.ts": GOALS_TEST,
        "features/goals/goals.feature": FEATURE,
    }
    if stack == "typescript":
        files["cucumber.mjs"] = (TEMPLATES / "typescript" / "cucumber.mjs").read_text(encoding="utf-8")
        files["features/steps/goals.steps.ts"] = CUCUMBER_STEPS
    else:
        files["playwright.config.ts"] = PLAYWRIGHT_CONFIG
        files["features/support/fixtures.ts"] = BDD_FIXTURES
        files["features/steps/goals.steps.ts"] = BDD_STEPS
    for path, text in files.items():
        write(target, path, text)
    commit(target, "Stride with its approved goal scenario")
    git(target, "switch", "-q", "-c", "feature")
    write(target, "src/goals.ts", GOALS + CLEAR_GOAL + "}\n")
    write(target, "test/clear-goal.test.ts", CLEAR_GOAL_TEST)
    commit(target, "Members can clear their goal")
    return target


def configure_stryker(repo: Path, **settings) -> None:
    """Sets keys of stryker.config.json, uncommitted."""
    path = repo / "stryker.config.json"
    path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), **settings}, indent=2) + "\n",
                    encoding="utf-8")


def run_gate(repo: Path, mode: str, *args: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("ATDD_BASE", "ATDD_OUT")}
    subprocess.run([sys.executable, "scripts/atdd/gate.py", mode, *args], cwd=repo, env=env, capture_output=True,
                   text=True)
    return json.loads((repo / ".atdd" / "quality" / "latest" / "report.json").read_text(encoding="utf-8"))
