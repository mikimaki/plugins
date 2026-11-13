#!/usr/bin/env python3
"""Runs the quality gate's command line against the throwaway .NET repo from dotnet_fixture.py and checks report.json.

    python3 tests/gate/dotnet_gate_test.py      # needs the .NET SDK 10, and diff-cover for the full-mode test
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dotnet_fixture as fixture  # noqa: E402
from fixture import check, failures  # noqa: E402

FAILING_PUSH_TEST = """using Steps.Push;
using Xunit;

namespace Steps.Push.Tests;

public class SlowGatewayTests
{
    [Fact]
    public void Gateway_answers_in_time() => Assert.Equal("the gateway answered in time", PushGateway.Url);
}
"""

FAILING_REPORT_TEST = """using Steps.Goals;
using Steps.Reports;
using Xunit;

namespace Steps.Reports.Tests;

public class SlowReportTests
{
    [Fact]
    public void Report_answers_in_time() => Assert.Equal("in time", WeeklyReport.Of(new GoalBook(), "robin"));
}
"""

FLAKY_PUSH_TEST = "Steps.Push.Tests.SlowGatewayTests.Gateway_answers_in_time"

GATEWAY_TIMEOUTS = """namespace Steps.Push.Tests;

public static class GatewayTimeouts
{
    public const int Seconds = 5;
}
"""

FAILING_PUSH_FEATURE = """Feature: Slow push

  Scenario: The gateway answers in time
    Given the push gateway
    Then the gateway answers in time
"""

FLAKY_PUSH_SCENARIO = "Steps.Acceptance.Features.SlowPushFeature.TheGatewayAnswersInTime"


class DotnetGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = fixture.build(Path(self.tmp.name) / "repo")

    def tearDown(self):
        self.tmp.cleanup()

    def test_failing_test_whose_project_reaches_no_changed_project_is_flaky(self):
        fixture.add_to_base(self.repo, "tests/Steps.Push.Tests/SlowGatewayTests.cs", FAILING_PUSH_TEST)
        report = fixture.run_gate(self.repo, "fast", "--only", "tests")
        tests = check(report, "tests")
        self.assertEqual("flaky", tests["status"], tests["detail"])
        self.assertIn(FLAKY_PUSH_TEST, tests["detail"])
        self.assertTrue(report["passed"], failures(report))

    def test_full_gate_measures_coverage_and_mutation_without_the_flaky_test_and_names_it(self):
        self.repo = fixture.build(Path(self.tmp.name) / "repo", with_stryker=True)
        fixture.add_to_base(self.repo, "tests/Steps.Push.Tests/SlowGatewayTests.cs", FAILING_PUSH_TEST)
        report = fixture.run_gate(self.repo, "full", "--only", "tests,diff-coverage,mutation")
        self.assertEqual("flaky", check(report, "tests")["status"], check(report, "tests")["detail"])
        for name in ("diff-coverage", "mutation"):
            self.assertEqual("pass", check(report, name)["status"], check(report, name)["detail"])
            self.assertIn(FLAKY_PUSH_TEST, check(report, name)["detail"])
        self.assertTrue(report["passed"], failures(report))
        self.assertNotIn("tests are failing", (self.repo / check(report, "mutation")["log"]).read_text(encoding="utf-8"))

    def test_failing_test_whose_project_references_a_changed_project_transitively_fails(self):
        fixture.add_to_base(self.repo, "tests/Steps.Reports.Tests/SlowReportTests.cs", FAILING_REPORT_TEST)
        self.assert_tests_fail(fixture.run_gate(self.repo, "fast", "--only", "tests"), "src/Steps.Goals/GoalBook.cs")

    def test_failing_test_whose_own_project_changed_on_the_branch_fails(self):
        fixture.add_to_base(self.repo, "tests/Steps.Push.Tests/SlowGatewayTests.cs", FAILING_PUSH_TEST)
        fixture.add_to_feature(self.repo, "tests/Steps.Push.Tests/GatewayTimeouts.cs", GATEWAY_TIMEOUTS)
        self.assert_tests_fail(fixture.run_gate(self.repo, "fast", "--only", "tests"),
                               "tests/Steps.Push.Tests/GatewayTimeouts.cs")

    def test_failing_scenario_whose_project_reaches_no_changed_project_is_flaky(self):
        fixture.add_to_base(self.repo, "tests/Steps.Acceptance/Features/SlowPush.feature", FAILING_PUSH_FEATURE)
        tests = check(fixture.run_gate(self.repo, "fast", "--only", "tests"), "tests")
        self.assertEqual("flaky", tests["status"], tests["detail"])
        self.assertIn(FLAKY_PUSH_SCENARIO, tests["detail"])

    def test_failing_scenario_whose_feature_file_changed_on_the_branch_fails(self):
        fixture.add_to_base(self.repo, "tests/Steps.Acceptance/Features/SlowPush.feature", FAILING_PUSH_FEATURE)
        fixture.add_to_feature(self.repo, "tests/Steps.Acceptance/Features/SlowPush.feature",
                               FAILING_PUSH_FEATURE.replace("  Scenario", "  @slow\n  Scenario"))
        self.assert_tests_fail(fixture.run_gate(self.repo, "fast", "--only", "tests"),
                               "tests/Steps.Acceptance/Features/SlowPush.feature")

    def test_build_error_fails_even_when_every_failing_test_is_flaky(self):
        fixture.add_to_base(self.repo, "tests/Steps.Push.Tests/SlowGatewayTests.cs", FAILING_PUSH_TEST)
        fixture.add_to_feature(self.repo, "src/Steps.Goals/Broken.cs", "namespace Steps.Goals;\n\npublic class Broken {\n")
        tests = check(fixture.run_gate(self.repo, "fast", "--only", "tests"), "tests")
        self.assertEqual("fail", tests["status"], tests["detail"])
        self.assertIn("src/Steps.Goals/Broken.cs", tests["detail"])

    def assert_tests_fail(self, report: dict, why: str):
        tests = check(report, "tests")
        self.assertEqual("fail", tests["status"], tests["detail"])
        self.assertIn("Not flaky:", tests["detail"])
        self.assertIn(why, tests["detail"].split("Not flaky:")[-1])
        self.assertFalse(report["passed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
