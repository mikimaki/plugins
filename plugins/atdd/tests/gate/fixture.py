"""A throwaway Python repo for the quality gate, built from the Python app of the cover-reports-discrepancy-not-code-change
eval: `main` holds the app, one approved .feature file bound by an acceptance test, and an import-linter contract;
`feature` adds GoalBook.clear_goal with its unit test, a change every check of the fast gate passes.

    repo = build(target)                  # target is created or replaced
    report = run_gate(repo, "fast")       # report.json as a dict; extra args go to gate.py
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
APP = PLUGIN / "evals" / "cover-reports-discrepancy-not-code-change" / "files" / "base"
GATE = PLUGIN / "skills" / "quality-gate" / "scripts" / "gate.py"

IMPORT_CONTRACT = """
[tool.importlinter]
root_package = "stepgoals"

[[tool.importlinter.contracts]]
name = "goals know nothing of the chat app"
type = "forbidden"
source_modules = ["stepgoals.goals"]
forbidden_modules = ["stepgoals.app"]
"""

FEATURE = """Feature: Daily step goal

  Scenario: A goal in range is saved
    Given Robin has no goal
    When Robin sets a goal of "8,000" steps
    Then Robin's goal is 8000 steps
"""

ACCEPTANCE = '''from pytest_bdd import given, parsers, scenarios, then, when

from stepgoals.goals import GoalBook

scenarios("goals")


@given("Robin has no goal", target_fixture="book")
def no_goal():
    return GoalBook()


@when(parsers.parse('Robin sets a goal of "{text}" steps'))
def set_goal(book, text):
    book.set_goal("robin", text)


@then(parsers.parse("Robin's goal is {steps:d} steps"))
def goal_is(book, steps):
    assert book.goal_of("robin") == steps
'''

CLEAR_GOAL = '''
    def clear_goal(self, member):
        self._goals.pop(member, None)
'''

CLEAR_GOAL_TEST = '''from stepgoals.goals import GoalBook


def test_cleared_goal_is_gone():
    book = GoalBook()
    book.set_goal("robin", "8,000")
    book.clear_goal("robin")
    assert book.goal_of("robin") is None


def test_clearing_a_member_without_a_goal_changes_nothing():
    book = GoalBook()
    book.clear_goal("robin")
    assert book.goal_of("robin") is None
'''


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def write(repo: Path, path: str, text: str, append: bool = False) -> None:
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a" if append else "w", encoding="utf-8") as handle:
        handle.write(text)


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)


def build(target: Path) -> Path:
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(APP, target, ignore=shutil.ignore_patterns(".atdd", "__pycache__"))
    (target / "dot-claude").rename(target / ".claude")
    (target / "dot-gitignore").rename(target / ".gitignore")
    (target / "scripts" / "atdd").mkdir(parents=True)
    shutil.copy(GATE, target / "scripts" / "atdd" / "gate.py")
    git(target, "init", "-q", "-b", "main")
    git(target, "config", "user.email", "fixture@example.com")
    git(target, "config", "user.name", "fixture")
    write(target, "pyproject.toml", IMPORT_CONTRACT, append=True)
    write(target, "features/goals/goals.feature", FEATURE)
    write(target, "tests/acceptance/test_goal_scenarios.py", ACCEPTANCE)
    commit(target, "Stride with its approved goal scenario")
    git(target, "switch", "-q", "-c", "feature")
    write(target, "src/stepgoals/goals.py", CLEAR_GOAL, append=True)
    write(target, "tests/unit/test_clear_goal.py", CLEAR_GOAL_TEST)
    commit(target, "Members can clear their goal")
    return target


def configure_gate(repo: Path, **settings) -> None:
    """Sets keys of the "gate" section in .claude/atdd.json, uncommitted."""
    path = repo / ".claude" / "atdd.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["gate"].update(settings)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def run_gate(repo: Path, mode: str, *args: str) -> dict:
    """Runs gate.py the way a hook or agent does and returns its report.json. The tools come from the PATH; the app's
    package is put on PYTHONPATH as an editable install would."""
    env = {**os.environ, "PYTHONPATH": str(repo / "src")}
    env.pop("ATDD_BASE", None)
    env.pop("ATDD_OUT", None)
    subprocess.run([sys.executable, "scripts/atdd/gate.py", mode, *args], cwd=repo, env=env, capture_output=True, text=True)
    return json.loads((repo / ".atdd" / "quality" / "latest" / "report.json").read_text(encoding="utf-8"))
