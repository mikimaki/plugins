#!/usr/bin/env python3
"""Runs the quality gate's command line against the throwaway TypeScript and Next.js repos from node_fixture.py and
checks how it classifies failing tests.

    python3 tests/gate/node_gate_test.py      # needs Node.js 22+, npm, and diff-cover for the full-mode test
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import node_fixture as fixture  # noqa: E402

FAILING_PUSH_TEST = """import { expect, it } from 'vitest'
import { PUSH_URL } from '../src/push.ts'

it('answers in time', () => {
  expect(PUSH_URL).toBe('the gateway answered in time')
})
"""

FAILING_APP_TEST = """import { describe, expect, it } from 'vitest'
import { App } from '../src/app.ts'

describe('app', () => {
  it('answers in time', () => {
    expect(App.name).toBe('the app answered in time')
  })
})
"""

PUSH_HELPER = """import { PUSH_URL } from '../src/push.ts'

export const gatewayUrl = (): string => PUSH_URL
"""

FAILING_HELPED_TEST = """import { expect, it } from 'vitest'
import { gatewayUrl } from './helpers.ts'

it('answers in time with help', () => {
  expect(gatewayUrl()).toBe('the gateway answered in time')
})
"""

PUSH_FEATURE = """Feature: Push messages

  Scenario: The gateway answers in time
    Given the push gateway
    Then the gateway answers in time
"""

PUSH_SCENARIO = "features/push/push.feature::The gateway answers in time"

CUCUMBER_PUSH_STEPS = """import assert from 'node:assert/strict'
import { Given, Then } from '@cucumber/cucumber'
import { PUSH_URL } from '../../src/push.ts'

let url = ''

Given('the push gateway', () => {
  url = PUSH_URL
})

Then('the gateway answers in time', () => {
  assert.equal(url, 'the gateway answered in time')
})
"""

BDD_PUSH_STEPS = """import { expect } from '@playwright/test'
import { PUSH_URL } from '../../src/push.ts'
import { Given, Then } from '../support/fixtures.ts'

let url = ''

Given('the push gateway', async () => {
  url = PUSH_URL
})

Then('the gateway answers in time', async () => {
  expect(url).toBe('the gateway answered in time')
})
"""


def check(report: dict, name: str) -> dict:
    return next(c for c in report["checks"] if c["name"] == name)


def failures(report: dict) -> str:
    return "\n".join(f"{c['name']}: {c['detail']}" for c in report["checks"] if c["status"] == "fail")


class TypeScriptGateTest(unittest.TestCase):
    stack = "typescript"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = fixture.build(Path(self.tmp.name) / "repo", self.stack)

    def tearDown(self):
        self.tmp.cleanup()

    def test_failing_test_unchanged_since_base_and_unrelated_to_the_change_is_flaky(self):
        fixture.add_to_base(self.repo, "test/push.test.ts", FAILING_PUSH_TEST)
        report = fixture.run_gate(self.repo, "fast")
        tests = check(report, "tests")
        self.assertEqual("flaky", tests["status"], tests["detail"])
        self.assertIn("test/push.test.ts::answers in time", tests["detail"])
        self.assertTrue(report["passed"], failures(report))

    def test_full_gate_measures_coverage_and_mutation_without_the_flaky_tests_and_names_them(self):
        fixture.add_to_base(self.repo, "test/push.test.ts", FAILING_PUSH_TEST.replace(
            "it('answers in time'", "it('has a gateway', () => {\n  expect(PUSH_URL).toBeTruthy()\n})\n\nit('answers in time'"))
        self.add_failing_push_scenario()
        fixture.configure_stryker(self.repo, vitest={"configFile": "vitest.config.mts", "related": False})
        report = fixture.run_gate(self.repo, "full")
        tests = check(report, "tests")
        self.assertEqual("flaky", tests["status"], tests["detail"])
        coverage, mutation = check(report, "diff-coverage"), check(report, "mutation")
        self.assertEqual("pass", coverage["status"], coverage["detail"])
        self.assertEqual("pass", mutation["status"], mutation["detail"])
        for flaky in ("test/push.test.ts::answers in time", PUSH_SCENARIO):
            self.assertIn(flaky, coverage["detail"])
            self.assertIn(flaky, mutation["detail"])
        self.assertTrue(report["passed"], failures(report))

    def test_failing_test_added_on_the_branch_fails(self):
        fixture.add_to_feature(self.repo, "test/push.test.ts", FAILING_PUSH_TEST)
        self.assert_tests_fail(fixture.run_gate(self.repo, "fast"), "added or changed on this branch")

    def test_failing_test_reaching_changed_production_code_through_other_modules_fails(self):
        fixture.add_to_base(self.repo, "test/app.test.ts", FAILING_APP_TEST)
        self.assert_tests_fail(fixture.run_gate(self.repo, "fast"), "imports reach changed production code: src/goals.ts")

    def test_failing_scenario_unchanged_since_base_and_unrelated_to_the_change_is_flaky(self):
        self.add_failing_push_scenario()
        report = fixture.run_gate(self.repo, "fast")
        tests = check(report, "tests")
        self.assertEqual("flaky", tests["status"], tests["detail"])
        self.assertIn(PUSH_SCENARIO, tests["detail"])
        self.assertTrue(report["passed"], failures(report))

    def test_failing_scenario_whose_feature_file_changed_on_the_branch_fails(self):
        self.add_failing_push_scenario()
        fixture.add_to_feature(self.repo, "features/push/push.feature", PUSH_FEATURE.replace("Push messages", "Push"))
        self.assert_tests_fail(fixture.run_gate(self.repo, "fast"), "features/push/push.feature added or changed")

    def test_failing_scenario_whose_steps_reach_changed_production_code_fails(self):
        self.add_failing_push_scenario(steps=self.push_steps().replace(
            "import { PUSH_URL }", "import { GoalBook } from '../../src/goals.ts'\nimport { PUSH_URL }"))
        self.assert_tests_fail(fixture.run_gate(self.repo, "fast"), "imports reach changed production code: src/goals.ts")

    def test_failing_scenario_whose_step_definitions_changed_on_the_branch_fails(self):
        self.add_failing_push_scenario()
        fixture.add_to_feature(self.repo, "features/steps/push.steps.ts", self.push_steps() + "\nexport const TIMEOUT = 5\n")
        self.assert_tests_fail(fixture.run_gate(self.repo, "fast"), "features/steps/push.steps.ts added or changed")

    def add_failing_push_scenario(self, steps: str | None = None):
        fixture.add_to_base(self.repo, "features/push/push.feature", PUSH_FEATURE)
        fixture.add_to_base(self.repo, "features/steps/push.steps.ts", steps or self.push_steps())

    def push_steps(self) -> str:
        return CUCUMBER_PUSH_STEPS

    def test_failing_test_using_a_helper_changed_on_the_branch_fails(self):
        fixture.add_to_base(self.repo, "test/helpers.ts", PUSH_HELPER)
        fixture.add_to_base(self.repo, "test/helped.test.ts", FAILING_HELPED_TEST)
        fixture.add_to_feature(self.repo, "test/helpers.ts", PUSH_HELPER + "\nexport const TIMEOUT = 5\n")
        self.assert_tests_fail(fixture.run_gate(self.repo, "fast"), "test/helpers.ts")

    def test_a_flaky_failure_beside_a_real_one_fails(self):
        fixture.add_to_base(self.repo, "test/push.test.ts", FAILING_PUSH_TEST)
        fixture.add_to_base(self.repo, "test/app.test.ts", FAILING_APP_TEST)
        self.assert_tests_fail(fixture.run_gate(self.repo, "fast"), "test/app.test.ts::app > answers in time")

    def assert_tests_fail(self, report: dict, why: str):
        tests = check(report, "tests")
        self.assertEqual("fail", tests["status"], tests["detail"])
        self.assertIn(why, tests["detail"])
        self.assertFalse(report["passed"])


class NextJsGateTest(TypeScriptGateTest):
    """The same cases with the Next.js stack, whose scenarios run in playwright-bdd."""
    stack = "nextjs"

    def push_steps(self) -> str:
        return BDD_PUSH_STEPS


if __name__ == "__main__":
    unittest.main(verbosity=2)
