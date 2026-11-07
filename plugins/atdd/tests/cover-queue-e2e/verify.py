#!/usr/bin/env python3
"""Checks a finished /atdd:cover-queue run on the fixture that build.sh made.

    verify.py <fixture-dir> [--snapshot FILE] [--unchanged-since FILE]

Exits 1 if any criterion fails. --snapshot writes the refs, the atdd git config and the gh calls to FILE;
--unchanged-since compares them with an earlier snapshot (the second run must change nothing).
"""
import json, re, subprocess, sys
from pathlib import Path

fixture = Path(sys.argv[1]).resolve()
repo, calls_file = fixture / "repo", fixture / "gh-calls.jsonl"
U = {name: f"atdd/cover-{name}" for name in ("a01-goals-accepted-goals", "a01-goals-goal-range",
                                              "a02-celebrations-below-the-goal", "a02-celebrations-reaching-the-goal")}
DONE = [U["a01-goals-accepted-goals"], U["a02-celebrations-below-the-goal"], U["a02-celebrations-reaching-the-goal"]]
PARKED = U["a01-goals-goal-range"]
INTEGRATION = "atdd/cover-base"
failures = []


def git(*args, check=True):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and result.returncode:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def expect(ok, message):
    print(("PASS " if ok else "FAIL ") + message)
    if not ok:
        failures.append(message)


def state():
    refs = git("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/remotes")
    config = git("config", "--get-regexp", r"^branch\..*\.atdd", check=False)
    remote = subprocess.run(["git", "--git-dir", str(fixture / "origin.git"), "for-each-ref",
                             "--format=%(refname) %(objectname)"], capture_output=True, text=True).stdout
    return {"refs": refs, "config": config, "origin": remote, "gh": calls_file.read_text() if calls_file.exists() else ""}


if "--unchanged-since" in sys.argv:
    before = json.loads(Path(sys.argv[sys.argv.index("--unchanged-since") + 1]).read_text())
    now = state()
    for key in before:
        expect(before[key] == now[key], f"the second run left {key} unchanged")
    sys.exit(1 if failures else 0)

status = {b: git("config", "--get", f"branch.{b}.atddStatus", check=False) for b in U.values()}
expect(all(status[b] == "done" for b in DONE), f"three units ended done: {status}")
expect(status[PARKED] == "parked", f"the contradicting unit is parked: {status[PARKED]!r}")

calls = [json.loads(line)["args"] for line in calls_file.read_text().splitlines() if line.strip()]
prs = [c for c in calls if c[:2] == ["pr", "create"]]
value = lambda c, *names: next((c[i + 1] for i, a in enumerate(c) for n in names if a == n and i + 1 < len(c)), None)
pairs = [(value(c, "--head", "-H"), value(c, "--base", "-B")) for c in prs]
expected = [(DONE[0], INTEGRATION), (DONE[1], DONE[0]), (DONE[2], DONE[1])]
expect(pairs == expected, f"PRs target the previous done unit, the first the integration branch: {pairs}")

top = DONE[-1]
definitions = []
for path in git("ls-tree", "-r", "--name-only", top, "--", "tests").splitlines():
    if path.endswith(".py"):
        text = git("show", f"{top}:{path}")
        definitions += [path for line in text.splitlines()
                        if re.search(r"@(given|when|then|step)\(", line) and "Stride member" in line]
expect(len(definitions) == 1, f"the shared step 'Robin is a Stride member' is defined once on {top}: {definitions}")

parked_files = git("diff", "--name-only", f"{DONE[0]}..{PARKED}").splitlines()
wip = git("log", "--format=%s", f"{DONE[0]}..{PARKED}")
expect(any(p.startswith("tests/") for p in parked_files) and "park" in wip,
       f"the parked unit's test files are committed on its branch: {parked_files} / {wip.splitlines()}")
expect(git("config", "--get", f"branch.{DONE[1]}.atddBase", check=False) == DONE[0]
       and subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", PARKED, DONE[1]]).returncode != 0,
       "the unit after the parked one started from the last done tip, not from the parked branch")

parents = {DONE[0]: INTEGRATION, PARKED: DONE[0], DONE[1]: DONE[0], DONE[2]: DONE[1]}
for branch, parent in parents.items():
    changed = git("diff", "--name-only", f"{parent}...{branch}").splitlines()
    outside = [p for p in changed if not (p.startswith("tests/") or p.startswith("features/"))]
    expect(not outside, f"{branch} changes nothing outside the test paths: {outside or changed}")

if "--snapshot" in sys.argv:
    Path(sys.argv[sys.argv.index("--snapshot") + 1]).write_text(json.dumps(state()))
sys.exit(1 if failures else 0)
