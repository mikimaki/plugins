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


def names(report: dict) -> list:
    return [check["name"] for check in report["checks"]]


def failures(report: dict) -> str:
    return "\n".join(f"{c['name']}: {c['detail']}" for c in report["checks"] if c["status"] == "fail")


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
