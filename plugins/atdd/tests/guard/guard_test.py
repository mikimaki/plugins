#!/usr/bin/env python3
"""Feeds scripts/atdd_guard.py synthetic PreToolUse payloads in a throwaway repo and checks allow/deny.

    python3 tests/guard/guard_test.py
"""
import json, subprocess, sys, tempfile
from pathlib import Path

GUARD = Path(__file__).resolve().parents[2] / "scripts" / "atdd_guard.py"
CONFIG = {"stack": "python", "baseBranch": "main", "featuresDir": "features",
          "lockedPaths": ["features/**/*.feature", "features/**/requirements.md", ".claude/atdd.json", "scripts/atdd/**"],
          "commands": {"coverFastChecks": "true", "fastChecks": "true"},
          "gate": {"sourceDirs": ["src"], "testDirs": ["tests"]}, "cover": {"integrationBranch": "atdd/cover-base"}}
UNIT, PARENT = "atdd/cover-a01-x", "atdd/cover-base"


def sh(root, *args):
    subprocess.run(args, cwd=root, check=True, capture_output=True)


def setup(root: Path):
    sh(root, "git", "init", "-q", "-b", "main")
    sh(root, "git", "config", "user.email", "t@example.com"); sh(root, "git", "config", "user.name", "t")
    (root / ".claude").mkdir(); (root / ".claude/atdd.json").write_text(json.dumps(CONFIG))
    for path, text in {"src/app.py": "x = 1\n", "tests/acceptance/conftest.py": "\n", "features/a01/x.feature": "Feature: x\n",
                       ".gitignore": ".atdd/\n"}.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True); (root / path).write_text(text)
    sh(root, "git", "add", "-A"); sh(root, "git", "commit", "-qm", "init")
    sh(root, "git", "branch", PARENT)
    sh(root, "git", "switch", "-q", "-c", UNIT, PARENT)
    sh(root, "git", "config", f"branch.{UNIT}.atddBase", PARENT)


def decide(root, mode, agent, tool_input):
    payload = {"agent_type": agent, "cwd": str(root), "tool_input": tool_input}
    out = subprocess.run([sys.executable, str(GUARD), mode], input=json.dumps(payload), capture_output=True, text=True).stdout
    return ("deny", json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]) if out.strip() else ("allow", "")


COVERER, PREPARER, REVIEWER, QA = "atdd:acceptance-test-coverer", "atdd:preparer", "atdd:coverage-reviewer", "atdd:coverage-qa"
failures = 0


def case(root, expected, mode, agent, value, label, before=None):
    global failures
    if before:
        before(root)
    tool_input = {"command": value} if mode in ("git", "gh") else {"file_path": str(root / value)}
    got, reason = decide(root, mode, agent, tool_input)
    ok = got == expected
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'} {expected:5} {label}" + ("" if ok else f"  (got {got}: {reason[:120]})"))


def dirty(*files):
    def make(root):
        for f in files:
            (root / f).parent.mkdir(parents=True, exist_ok=True)
            (root / f).write_text("changed\n")
    return make


def clean(root):
    sh(root, "git", "reset", "-q", "--hard"); sh(root, "git", "clean", "-qfd")


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    setup(root)
    E = lambda *a, **k: case(root, *a, **k)
    # edits
    E("allow", "edit", COVERER, "tests/acceptance/test_a01.py", "coverer edits inside the test paths")
    E("deny", "edit", COVERER, "src/app.py", "coverer edits production code")
    E("deny", "edit", COVERER, "pyproject.toml", "coverer edits a manifest")
    E("deny", "edit", COVERER, "features/a01/x.feature", "coverer edits a locked scenario file")
    E("allow", "edit", PREPARER, ".atdd/a01/cover/x/pr-body.md", "preparer writes the PR body under .atdd/")
    E("deny", "edit", PREPARER, "tests/acceptance/conftest.py", "preparer edits a test file")
    E("deny", "edit", REVIEWER, "tests/acceptance/conftest.py", "reviewer edits anything")
    # park commit: the preparer commits only test paths
    E("allow", "git", PREPARER, "git commit -m 'wip(atdd): park a01/x' -- tests/acceptance/test_a01.py",
      "preparer parks a new test file", before=dirty("tests/acceptance/test_a01.py"))
    E("deny", "git", PREPARER, "git commit -m wip -- src/app.py", "preparer commits production code", before=dirty("src/app.py"))
    E("deny", "git", PREPARER, "git commit -am wip", "preparer commits everything with -a", before=dirty("src/app.py"))
    E("deny", "git", PREPARER, "git commit -m wip", "preparer commits a staged production file",
      before=lambda r: (dirty("src/app.py")(r), sh(r, "git", "add", "src/app.py")))
    E("deny", "git", PREPARER, "git commit --amend -m wip", "preparer amends")
    clean(root)
    # the coverer's commit hook: nothing outside the test paths, then the cover fast checks
    E("deny", "git", COVERER, "git commit -m 'test(a01): cover x'", "coverer commits while production code is modified",
      before=dirty("src/app.py", "tests/acceptance/test_a01.py"))
    clean(root)
    E("allow", "git", COVERER, "git commit -m 'test(a01): cover x'", "coverer commits test files only",
      before=dirty("tests/acceptance/test_a01.py"))
    clean(root)
    E("allow", "git", PREPARER, "git checkout inventory -- features/a01/requirements.md features/a01/x.feature",
      "preparer brings scenarios in from the source ref")
    E("deny", "git", PREPARER, "git checkout inventory -- docs/specs/a01.md", "preparer brings the spec into the branch")
    E("deny", "git", PREPARER, "git checkout main", "preparer switches with checkout")
    E("allow", "git", PREPARER, f"git switch -c atdd/cover-a01-y {UNIT}", "preparer cuts the next unit branch")
    E("allow", "git", PREPARER, f"git config branch.{UNIT}.atddStatus parked", "preparer records a verdict")
    E("allow", "git", PREPARER, f"git config --unset branch.{UNIT}.atddReason", "preparer clears a reason")
    E("deny", "git", PREPARER, "git config core.hooksPath /tmp/x", "preparer sets other git config")
    E("allow", "git", PREPARER, "git branch atdd/cover-base origin/main", "preparer creates the integration branch")
    E("deny", "git", PREPARER, "git branch -f atdd/cover-base HEAD", "preparer moves a branch")
    E("deny", "git", PREPARER, "git branch -D atdd/cover-a01-x", "preparer deletes a branch")
    E("deny", "git", PREPARER, "git reset --hard HEAD~1", "preparer resets")
    E("allow", "git", PREPARER, "git fetch origin", "preparer fetches")
    E("deny", "git", PREPARER, "git fetch origin main:main", "preparer fetches into a local branch")
    # push
    E("allow", "git", PREPARER, f"git push origin {UNIT}", "preparer pushes a queue unit branch")
    E("deny", "git", PREPARER, "git push origin main", "preparer pushes the base branch")
    E("deny", "git", PREPARER, f"git push origin {PARENT}", "preparer pushes the integration branch")
    E("deny", "git", PREPARER, f"git push --force origin {UNIT}", "preparer force-pushes")
    E("deny", "git", PREPARER, f"git push --force-with-lease origin {UNIT}", "preparer force-pushes with lease")
    E("deny", "git", PREPARER, f"git push origin +{UNIT}", "preparer force-pushes with +")
    E("deny", "git", PREPARER, f"git push origin --delete {UNIT}", "preparer deletes a remote branch")
    E("deny", "git", PREPARER, f"git push origin HEAD:main", "preparer pushes HEAD onto main")
    E("deny", "git", PREPARER, "git push origin feature/other", "preparer pushes a branch the queue didn't create")
    E("deny", "git", COVERER, f"git push origin {UNIT}", "coverer pushes")
    E("deny", "git", REVIEWER, f"git push origin {UNIT}", "reviewer pushes")
    # rebase: only an unpushed unit branch
    E("allow", "git", PREPARER, f"git rebase {PARENT}", "preparer rebases an unpushed unit branch")
    E("deny", "git", PREPARER, f"git rebase -i {PARENT}", "preparer rebases interactively")
    E("deny", "git", COVERER, f"git rebase {PARENT}", "coverer rebases")
    sh(root, "git", "update-ref", f"refs/remotes/origin/{UNIT}", "HEAD")
    E("deny", "git", PREPARER, f"git rebase {PARENT}", "preparer rebases a unit branch that was pushed")
    # gh
    E("allow", "gh", PREPARER, f"gh pr create --base {PARENT} --head {UNIT} --title t --body-file .atdd/b.md", "preparer opens a unit PR")
    E("deny", "gh", PREPARER, "gh pr create --base main --head main --title t --body x", "preparer opens a PR for main")
    E("deny", "gh", PREPARER, f"gh pr create --head {UNIT} --title t --body x", "preparer opens a PR without --base")
    E("deny", "gh", PREPARER, "gh pr merge 12 --merge", "preparer merges a PR")
    E("deny", "gh", PREPARER, "gh api -X POST repos/o/r/pulls -f title=x", "preparer writes through gh api")
    E("deny", "gh", COVERER, f"gh pr create --base {PARENT} --head {UNIT} --title t --body x", "coverer opens a PR")
    E("deny", "gh", QA, "gh pr comment 12 --body hi", "QA comments on a PR")
    E("allow", "gh", REVIEWER, "gh pr view 12", "reviewer reads a PR")
    E("allow", "gh", PREPARER, "gh pr list --head atdd/cover-a01-x --json url", "preparer lists PRs")
    E("allow", "gh", PREPARER, "gh auth status", "preparer checks gh auth")
    # other sessions are untouched
    E("allow", "git", "", "git push --force origin main", "the main session isn't policed")

print(f"\n{'all passed' if not failures else f'{failures} failed'}")
sys.exit(1 if failures else 0)
