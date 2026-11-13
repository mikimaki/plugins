#!/usr/bin/env python3
"""Runs the quality gate's command line against the throwaway repo from fixture.py and checks report.json.

    PATH="<venv with the Python stack's tools>/bin:$PATH" python3 tests/gate/gate_test.py
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixture  # noqa: E402

FAST_STRUCTURAL_CHECKS = ["duplication", "architecture", "dead-code", "locked-files"]
FULL_ONLY = ["diff-coverage", "mutation", "vulnerabilities"]

FAILING_PUSH_TEST = """from stepgoals import push_gateway


def test_push_gateway_answers_in_time():
    assert push_gateway.PUSH_URL == "the gateway answered in time"
"""

FAILING_GOALS_TEST = """from stepgoals.goals import GoalBook


def test_goal_book_answers_in_time():
    assert GoalBook().goal_of("robin") == "the goal book answered in time"
"""

PUSH_FEATURE = """Feature: Push messages

  Scenario: The gateway answers in time
    Given the push gateway
    Then the gateway answers in time
"""

FAILING_PUSH_SCENARIOS = """from pytest_bdd import given, scenarios, then

from stepgoals import push_gateway

scenarios("push")


@given("the push gateway", target_fixture="url")
def gateway():
    return push_gateway.PUSH_URL


@then("the gateway answers in time")
def answers(url):
    assert url == "the gateway answered in time"
"""

PUSH_SCENARIO = "the push gateway answers in time"

PUSH_HELPER = """from stepgoals import push_gateway


def gateway_url():
    return push_gateway.PUSH_URL
"""

GOALS_HELPER = """from stepgoals.goals import GoalBook


def new_book():
    return GoalBook()
"""

FAILING_HELPED_TEST = """import helpers


def test_helped_check_answers_in_time():
    assert helpers.__name__ == "the helper answered in time"
"""

UNPARSEABLE_MODULE = "def broken(:\n    pass\n"

FAILING_LAZY_IMPORT_TEST = """def unused():
    from stepgoals import legacy
    return legacy


def test_legacy_answers_in_time():
    assert unused is None
"""

FAILING_APP_TEST = """from stepgoals.app import App


def test_app_answers_in_time():
    assert App().get_goal("robin") == {"status": "the app answered in time"}
"""


def names(report: dict) -> list:
    return [check["name"] for check in report["checks"]]


def failures(report: dict) -> str:
    return "\n".join(f"{c['name']}: {c['detail']}" for c in report["checks"] if c["status"] == "fail")


def check(report: dict, name: str) -> dict:
    return next(c for c in report["checks"] if c["name"] == name)


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = fixture.build(Path(self.tmp.name) / "repo")

    def tearDown(self):
        self.tmp.cleanup()

    def test_fast_gate_passes_its_structural_checks(self):
        report = fixture.run_gate(self.repo, "fast")
        statuses = {c["name"]: c["status"] for c in report["checks"]}
        self.assertEqual({name: "pass" for name in FAST_STRUCTURAL_CHECKS},
                         {name: statuses.get(name) for name in FAST_STRUCTURAL_CHECKS})
        self.assertTrue(report["passed"], failures(report))

    def test_fast_gate_leaves_out_the_slow_checks(self):
        self.assertEqual([], [n for n in names(fixture.run_gate(self.repo, "fast")) if n in FULL_ONLY])

    def test_full_gate_runs_the_slow_and_structural_checks(self):
        ran = names(fixture.run_gate(self.repo, "full"))
        self.assertEqual([], [n for n in FULL_ONLY + FAST_STRUCTURAL_CHECKS if n not in ran])

    def test_repo_configured_fast_checks_override_the_defaults(self):
        fixture.configure_gate(self.repo, fast=["lint", "tests"])
        self.assertEqual(["lint", "tests"], names(fixture.run_gate(self.repo, "fast")))


    def test_failing_test_unchanged_since_base_and_unrelated_to_the_change_is_flaky(self):
        fixture.add_to_base(self.repo, "tests/unit/test_push.py", FAILING_PUSH_TEST)
        report = fixture.run_gate(self.repo, "fast")
        tests = check(report, "tests")
        self.assertEqual("flaky", tests["status"], tests["detail"])
        self.assertIn("tests/unit/test_push.py::test_push_gateway_answers_in_time", tests["detail"])
        self.assertTrue(report["passed"], failures(report))


    def test_full_gate_measures_coverage_and_mutation_without_the_flaky_test_and_names_it(self):
        fixture.add_to_base(self.repo, "tests/unit/test_push.py", FAILING_PUSH_TEST)
        report = fixture.run_gate(self.repo, "full")
        flaky = "tests/unit/test_push.py::test_push_gateway_answers_in_time"
        self.assertEqual("flaky", check(report, "tests")["status"])
        coverage, mutation = check(report, "diff-coverage"), check(report, "mutation")
        self.assertEqual("pass", coverage["status"], coverage["detail"])
        self.assertIn(flaky, coverage["detail"])
        self.assertNotIn("mutmut failed", mutation["detail"])
        self.assertIn("killed", mutation["metric"], mutation["detail"])
        self.assertIn(flaky, mutation["detail"])

    def test_failing_test_added_on_the_branch_fails(self):
        fixture.add_to_feature(self.repo, "tests/unit/test_push.py", FAILING_PUSH_TEST)
        self.assert_tests_fail(fixture.run_gate(self.repo, "fast"), "added or changed on this branch")

    def test_failing_test_changed_on_the_branch_fails(self):
        fixture.add_to_base(self.repo, "tests/unit/test_push.py", FAILING_PUSH_TEST.replace("in time", "promptly"))
        fixture.add_to_feature(self.repo, "tests/unit/test_push.py", FAILING_PUSH_TEST)
        self.assert_tests_fail(fixture.run_gate(self.repo, "fast"), "added or changed on this branch")

    def test_failing_test_importing_changed_production_code_fails(self):
        fixture.add_to_base(self.repo, "tests/unit/test_goal_book.py", FAILING_GOALS_TEST)
        self.assert_tests_fail(fixture.run_gate(self.repo, "fast"), "src/stepgoals/goals.py")

    def test_failing_test_reaching_changed_production_code_through_other_modules_fails(self):
        fixture.add_to_base(self.repo, "tests/unit/test_app.py", FAILING_APP_TEST)
        self.assert_tests_fail(fixture.run_gate(self.repo, "fast"), "src/stepgoals/goals.py")

    def test_a_flaky_failure_beside_a_real_one_fails(self):
        fixture.add_to_base(self.repo, "tests/unit/test_push.py", FAILING_PUSH_TEST)
        fixture.add_to_base(self.repo, "tests/unit/test_goal_book.py", FAILING_GOALS_TEST)
        self.assert_tests_fail(fixture.run_gate(self.repo, "fast"), "tests/unit/test_goal_book.py::test_goal_book_answers_in_time")

    def test_failing_scenario_whose_feature_file_changed_on_the_branch_fails(self):
        fixture.add_to_base(self.repo, "features/push/push.feature", PUSH_FEATURE)
        fixture.add_to_base(self.repo, "tests/acceptance/test_push_scenarios.py", FAILING_PUSH_SCENARIOS)
        fixture.add_to_feature(self.repo, "features/push/push.feature", PUSH_FEATURE.replace("Push messages", "Push"))
        self.assert_tests_fail(fixture.run_gate(self.repo, "fast"), "features/push/push.feature")

    def test_failing_scenario_stays_flaky_when_only_another_feature_file_changed(self):
        fixture.add_to_base(self.repo, "features/push/push.feature", PUSH_FEATURE)
        fixture.add_to_base(self.repo, "tests/acceptance/test_push_scenarios.py", FAILING_PUSH_SCENARIOS)
        fixture.add_to_feature(self.repo, "features/goals/goals.feature", fixture.FEATURE.replace("Daily", "A daily"))
        tests = check(fixture.run_gate(self.repo, "fast"), "tests")
        self.assertEqual("flaky", tests["status"], tests["detail"])

    def test_failing_test_reaching_changed_production_code_through_a_test_helper_fails(self):
        fixture.add_to_base(self.repo, "tests/unit/helpers.py", GOALS_HELPER)
        fixture.add_to_base(self.repo, "tests/unit/test_helped.py", FAILING_HELPED_TEST)
        self.assert_tests_fail(fixture.run_gate(self.repo, "fast"), "src/stepgoals/goals.py")

    def test_failing_test_using_a_helper_changed_on_the_branch_fails(self):
        fixture.add_to_base(self.repo, "tests/unit/helpers.py", PUSH_HELPER)
        fixture.add_to_base(self.repo, "tests/unit/test_helped.py", FAILING_HELPED_TEST)
        fixture.add_to_feature(self.repo, "tests/unit/helpers.py", PUSH_HELPER + "\n\nTIMEOUT = 5\n")
        self.assert_tests_fail(fixture.run_gate(self.repo, "fast"), "tests/unit/helpers.py")

    def test_failing_test_whose_imports_cannot_be_parsed_fails_and_names_the_file(self):
        fixture.add_to_base(self.repo, "src/stepgoals/legacy.py", UNPARSEABLE_MODULE)
        fixture.add_to_base(self.repo, "tests/unit/test_legacy.py", FAILING_LAZY_IMPORT_TEST)
        self.assert_tests_fail(fixture.run_gate(self.repo, "fast"), "src/stepgoals/legacy.py")

    def assert_tests_fail(self, report: dict, why: str):
        tests = check(report, "tests")
        self.assertEqual("fail", tests["status"], tests["detail"])
        self.assertIn(why, tests["detail"])
        self.assertFalse(report["passed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
