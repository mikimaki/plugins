#!/usr/bin/env python3
"""Read-only helper for /atdd:cover-queue: what the queue holds and where each unit stands, derived from git.

    cover_queue.py plan --source <ref> [--area <key> ...] [--unit <key>/<file>.feature ...] [--open-prs] [--summary]
    cover_queue.py snapshot --source <ref> --area <key>

plan prints JSON: the queue's units in run order (areas in the order given, files by name), each with its unit
branch and status, the top of the stack the next unit is cut from, and every problem that would stop the whole queue.
It changes nothing in the repository; the preparer creates branches, commits and records progress with git, where
the atdd guard checks every command. --summary prints only, per area, how many of its units are done or parked and,
once every unit of the area has run, the ledger's requirements that no done unit covers.

A unit is one .feature file of features/<key>/ on the source ref. Its status comes from git alone:
- done: the integration branch already has the unit's .feature file (its PR was merged), or the unit branch has
  branch.<branch>.atddStatus = done;
- parked: branch.<branch>.atddStatus = parked, with the reason in branch.<branch>.atddReason;
- interrupted: the unit branch exists without a status (a run stopped in the middle of it);
- pending: no unit branch yet.

snapshot writes .atdd/<key>/spec.md, verbatim, from the source ref's copy of the spec the ledger names on its
"Source:" line, and .atdd/<key>/issue.json, unless the spec snapshot already exists. It writes nothing else.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ in the plugin's folders
HERE = Path(__file__).resolve().parent
DEFAULT_INTEGRATION_BRANCH = "atdd/cover-base"
MIN_GATE_VERSION = (0, 4, 0)
SOURCE_LINE = re.compile(r"^\s*(?:\*\*)?Source(?:\*\*)?\s*:\s*`?([^`\s]+)`?", re.IGNORECASE | re.MULTILINE)


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


guard = load("atdd_guard", HERE / "atdd_guard.py")  # the plugin's one definition of the test paths
gherkin = load("gherkin_check", HERE.parent / "skills" / "gherkin-style" / "scripts" / "gherkin_check.py")


class Repo:
    def __init__(self, root: Path) -> None:
        self.root = root

    def run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True)

    def out(self, *args: str) -> str:
        return self.run(*args).stdout.strip()

    def ok(self, *args: str) -> bool:
        return self.run(*args).returncode == 0

    def commit(self, ref: str) -> str | None:
        result = self.run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        return result.stdout.strip() if result.returncode == 0 else None

    def config(self, key: str) -> str:
        return self.out("config", "--get", key)

    def is_ancestor(self, a: str, b: str) -> bool:
        return self.ok("merge-base", "--is-ancestor", a, b)

    def has_path(self, ref: str, path: str) -> bool:
        return self.ok("cat-file", "-e", f"{ref}:{path}")


def newest(repo: Repo, branch: str, remote: str) -> tuple:
    """(ref, note): of <branch> and <remote>/<branch>, the one that contains the other."""
    local, far = f"refs/heads/{branch}", f"refs/remotes/{remote}/{branch}"
    local_sha, far_sha = repo.commit(local), repo.commit(far)
    if not local_sha and not far_sha:
        return None, ""
    if not far_sha or local_sha == far_sha:
        return branch, ""
    if not local_sha:
        return f"{remote}/{branch}", ""
    if repo.is_ancestor(local_sha, far_sha):
        return f"{remote}/{branch}", f"{branch} is behind {remote}/{branch}; using {remote}/{branch}."
    if repo.is_ancestor(far_sha, local_sha):
        return branch, f"{branch} is ahead of {remote}/{branch}; using {branch}."
    return branch, f"{branch} and {remote}/{branch} have diverged; using {branch}."


def unit_branch(key: str, stem: str) -> str:
    """atdd/cover-<key>-<stem>: never nested under another unit's or the integration branch's name."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{key}-{stem}").strip("-.")
    return f"atdd/cover-{safe}"


def area_files(repo: Repo, ref: str, features: str, key: str) -> list:
    listing = repo.out("ls-tree", "--name-only", ref, "--", f"{features}/{key}/")
    return sorted(Path(p).name for p in listing.splitlines() if p.endswith(".feature"))


def gate_version(root: Path) -> tuple | None:
    gate = root / "scripts" / "atdd" / "gate.py"
    if not gate.is_file():
        return None
    result = subprocess.run([sys.executable, str(gate), "--version"], cwd=root, capture_output=True, text=True)
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", result.stdout)
    return tuple(int(n) for n in match.groups()) if result.returncode == 0 and match else None


def coverage_setup_problem(root: Path, config: dict) -> str | None:
    """What the gate's acceptance-coverage check needs, checked before a single unit runs."""
    gate = config.get("gate") or {}
    if (gate.get("commands") or {}).get("acceptanceCoverage"):
        return None
    stack = config.get("stack", "")
    if stack in ("typescript", "nextjs"):
        if not (root / "scripts" / "atdd" / "coverage.mjs").is_file():
            return ("scripts/atdd/coverage.mjs is missing, so the gate can't measure the acceptance tests' coverage. "
                    "Run /atdd:init (or set gate.commands.acceptanceCoverage) and commit it on the base branch.")
    elif stack == "python":
        python = shutil.which("python3") or sys.executable
        if subprocess.run([python, "-c", "import pytest_cov"], cwd=root, capture_output=True).returncode != 0:
            return ("pytest-cov isn't installed for python3, so the gate can't measure the acceptance tests' coverage. "
                    "Install it (it's in the repo's dev dependencies), or set gate.commands.acceptanceCoverage.")
    elif stack == "dotnet":
        projects = [p for d in gate.get("testDirs", []) for p in (root / d).rglob("*.csproj")]
        if not any("coverlet.collector" in p.read_text(encoding="utf-8", errors="replace") for p in projects):
            return ("No test project under gate.testDirs references coverlet.collector, so the gate can't measure the "
                    "acceptance tests' coverage. Add it on the base branch, or set gate.commands.acceptanceCoverage.")
    return None


def describe_unit(repo: Repo, source, features: str, key: str, name: str, info: dict, integration_ref: str | None) -> dict:
    """One .feature file as a unit: its branch, its requirements and Gherkin errors, and its status from git."""
    path = f"{features}/{key}/{name}"
    stem = name[:-len(".feature")]
    unit = {"key": key, "file": name, "feature": path, "stem": stem, "branch": unit_branch(key, stem),
            "requirements": [], "checkErrors": [], "scenarios": 0}
    if not info["problems"]:
        result = gherkin.run(Path(features) / key, Path(info["ledger"]), 8,
                             gherkin.find_glossary(Path(features) / key, source), source, [Path(path)])
        info["requirements"] = result["ledger"]["requirements"]
        unit["scenarios"] = result["summary"]["scenarios"]
        unit["requirements"] = next(iter(result["fileRequirements"].values()), [])
        unit["checkErrors"] = [f"{i['file']}:{i['line']} [{i['code']}] {i['message']}"
                               for i in result["issues"] if i["level"] == "error"]
        if result["ledger"]["openQuestions"]:
            unit["checkErrors"].append(f"{info['ledger']} has open questions; answer them in the spec first: "
                                       + "; ".join(result["ledger"]["openQuestions"][:5]))
    branch = unit["branch"]
    sha = repo.commit(f"refs/heads/{branch}")
    status = repo.config(f"branch.{branch}.atddStatus") if sha else ""
    unit.update({"sha": sha or "", "parent": repo.config(f"branch.{branch}.atddBase") if sha else "",
                 "pr": repo.config(f"branch.{branch}.atddPr") if sha else "",
                 "reason": repo.config(f"branch.{branch}.atddReason") if sha else ""})
    if integration_ref and repo.has_path(integration_ref, path):
        unit["status"], unit["merged"] = "done", True
    elif sha and status in ("done", "parked"):
        unit["status"], unit["merged"] = status, False
    elif sha:
        unit["status"], unit["merged"] = "interrupted", False
    else:
        unit["status"], unit["merged"] = "pending", False
    return unit


def area_summary(key: str, info: dict) -> dict:
    """How far an area has got; once every unit of it has run, the requirements no done unit covers."""
    units = info.get("units", [])
    done = [u for u in units if u["status"] == "done"]
    parked = [u for u in units if u["status"] == "parked"]
    complete = bool(units) and len(done) + len(parked) == len(units)
    covered = {r for u in done for r in u["requirements"]}
    return {"key": key, "complete": complete, "units": len(units), "done": len(done), "parked": len(parked),
            "uncoveredRequirements": [r for r in info.get("requirements", []) if r not in covered] if complete else [],
            "problems": info.get("problems", [])}


def dirty_paths(repo: Repo) -> list:
    lines = repo.run("status", "--porcelain=v1", "--untracked-files=all").stdout.splitlines()
    return [line[3:] for line in lines if len(line) > 3 and not line[3:].startswith(".atdd/")]


def plan(args) -> dict:
    root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip())
    repo = Repo(root)
    config_path = root / ".claude" / "atdd.json"
    if not config_path.is_file():
        return {"ok": False, "problems": ["No .claude/atdd.json in this repo. Run /atdd:init first."], "units": []}
    config = json.loads(config_path.read_text(encoding="utf-8"))
    cover = config.get("cover") or {}
    features = str(config.get("featuresDir") or "features").strip("/")
    base_branch = str(config.get("baseBranch") or "main")
    integration = str(cover.get("integrationBranch") or DEFAULT_INTEGRATION_BRANCH)
    remote = str(cover.get("remote") or "origin")
    open_prs = bool(args.open_prs or cover.get("openPrs"))
    problems, notes = [], []

    if not repo.commit(args.source):
        problems.append(f"The source ref {args.source} isn't a commit, branch or tag in this repo.")
        return {"ok": False, "problems": problems, "units": []}

    # ---- the queue's units, in run order
    selected = {}
    for unit in args.unit or []:
        key, _, name = unit.strip("/").partition("/")
        if key.startswith(features + "/"):
            key, _, name = name.partition("/")
        selected.setdefault(key, set()).add(Path(name).name)
    areas = list(dict.fromkeys([*(args.area or []), *selected]))
    if not areas:
        problems.append("Name at least one area (a folder under the features directory) or unit to cover.")
    units, area_info = [], {}
    for key in areas:
        ledger = f"{features}/{key}/requirements.md"
        files = area_files(repo, args.source, features, key)
        info = area_info.setdefault(key, {"ledger": ledger, "files": files, "requirements": [], "problems": []})
        if not repo.has_path(args.source, ledger):
            info["problems"].append(f"{args.source} has no {ledger}.")
        if not files:
            info["problems"].append(f"{args.source} has no .feature files in {features}/{key}/.")
        info["allFiles"] = list(files)
        wanted = selected.get(key)
        if wanted:
            missing = sorted(wanted - set(files))
            if missing:
                info["problems"].append(f"Not in {args.source}:{features}/{key}/: {', '.join(missing)}")
            files = [f for f in files if f in wanted]
        info["selected"] = files

    integration_ref, integration_note = newest(repo, integration, remote)
    if integration_note:
        notes.append(integration_note)
    source = gherkin.GitRef(args.source, root)
    for key, info in area_info.items():
        info["units"] = []
        for name in info["allFiles"]:
            unit = describe_unit(repo, source, features, key, name, info, integration_ref)
            info["units"].append(unit)
            if name in info["selected"]:
                units.append(unit)

    # ---- the top of the stack: the done, unmerged unit branch that contains every other one
    done = []
    for line in repo.out("for-each-ref", "--format=%(refname:short)", "refs/heads/atdd/").splitlines():
        if repo.config(f"branch.{line}.atddStatus") != "done" or not repo.config(f"branch.{line}.atddBase"):
            continue
        merged = integration_ref and repo.is_ancestor(line, integration_ref)
        unit = next((u for u in units if u["branch"] == line), None)
        if unit and unit["merged"]:
            merged = True
        if not merged:
            done.append(line)
    tops = [b for b in done if all(repo.is_ancestor(other, b) for other in done)]
    top = tops[0] if tops else (integration if not done else "")
    if done and not tops:
        problems.append("The done unit branches don't form one stack (none of them contains all the others): "
                        + ", ".join(done) + ". Rebase or merge them into one line before the queue continues.")

    # ---- what would stop every unit
    dirty = dirty_paths(repo)
    if dirty:
        problems.append("The working tree has uncommitted changes outside .atdd/; commit or remove them first:\n"
                        + "\n".join(dirty[:20]))
    base_ref, base_note = newest(repo, base_branch, remote)
    if base_note:
        notes.append(base_note)
    if not base_ref:
        problems.append(f"Neither {base_branch} nor {remote}/{base_branch} exists (baseBranch in .claude/atdd.json).")
    test_paths = guard.cover_test_paths(config)
    if not test_paths:
        problems.append("No test paths. Set gate.testDirs (or cover.testPaths) in .claude/atdd.json.")
    if not features or not any(guard.matches(f"{features}/x/y.feature", p) for p in test_paths):
        problems.append(f"The features directory ({features}/) isn't inside the test paths, so a unit's scenarios "
                        "commit would count as a production change. Add it to cover.testPaths.")
    version = gate_version(root)
    if version is None or version < MIN_GATE_VERSION:
        problems.append(f"The repo's quality gate ({'missing' if version is None else '.'.join(map(str, version))}) "
                        "predates /atdd:cover-queue, which needs its duplicate-steps check. Copy the atdd plugin's "
                        "skills/quality-gate/scripts/gate.py over scripts/atdd/gate.py and commit it on "
                        f"{base_branch} (committed on a unit branch it would fail production-untouched).")
    commands = config.get("commands") or {}
    if not commands.get("acceptanceTests"):
        problems.append("commands.acceptanceTests isn't set in .claude/atdd.json; the gate runs the acceptance tests on "
                        "their own.")
    coverage_problem = coverage_setup_problem(root, config)
    if coverage_problem:
        problems.append(coverage_problem)

    integration_action = "exists"
    if not repo.commit(f"refs/heads/{integration}"):
        start = f"{remote}/{integration}" if repo.commit(f"refs/remotes/{remote}/{integration}") else base_ref
        integration_action = f"create from {start}" if start else "missing"
    on_remote = None
    if open_prs:
        if not shutil.which("gh"):
            problems.append("openPrs is on, but the GitHub CLI (gh) isn't on the PATH.")
        elif subprocess.run(["gh", "auth", "status"], cwd=root, capture_output=True).returncode != 0:
            problems.append("openPrs is on, but gh isn't signed in (gh auth status fails).")
        if not repo.ok("remote", "get-url", remote):
            problems.append(f"openPrs is on, but the repo has no remote named {remote}.")
        else:
            on_remote = bool(repo.out("ls-remote", "--heads", remote, integration))
            if not on_remote:
                start = repo.commit(f"{remote}/{base_branch}") or repo.commit(base_branch) or ""
                problems.append(f"openPrs is on, but {remote} has no {integration} branch, so the first unit's pull "
                                f"request would have no base. The queue never pushes the integration branch; create it "
                                f"once yourself: git push {remote} {start[:12] or remote + '/' + base_branch}:refs/heads/{integration}")

    for key, info in area_info.items():
        for problem in info["problems"]:
            notes.append(f"{key}: {problem}")
    summaries = [area_summary(key, info) for key, info in area_info.items()]
    if args.summary:
        return {"ok": True, "areas": summaries}
    return {
        "ok": not problems,
        "problems": problems,
        "notes": notes,
        "source": args.source,
        "sourceSha": repo.commit(args.source),
        "baseBranch": base_branch,
        "baseRef": base_ref or "",
        "integrationBranch": integration,
        "integration": {"ref": integration_ref or "", "action": integration_action, "onRemote": on_remote},
        "remote": remote,
        "openPrs": open_prs,
        "featuresDir": features,
        "testPaths": test_paths,
        "maxConsecutiveParks": int(cover.get("maxConsecutiveParks") or 3),
        "maxRounds": int((config.get("limits") or {}).get("coverRounds") or 5),
        "hasQa": bool(commands.get("qaUp")),
        "top": top,
        "doneUnmerged": done,
        "pluginRoot": str(HERE.parent),
        "areas": summaries,
        "units": [{k: v for k, v in u.items() if k != "requirements"} for u in units],
    }


def snapshot(args) -> dict:
    root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip())
    repo = Repo(root)
    config = json.loads((root / ".claude" / "atdd.json").read_text(encoding="utf-8"))
    features = str(config.get("featuresDir") or "features").strip("/")
    target = root / ".atdd" / args.area / "spec.md"
    if target.is_file():
        return {"ok": True, "specPath": f".atdd/{args.area}/spec.md", "written": False}
    ledger = f"{features}/{args.area}/requirements.md"
    ledger_text = repo.run("show", f"{args.source}:{ledger}")
    if ledger_text.returncode != 0:
        return {"ok": False, "reason": f"{args.source} has no {ledger}."}
    match = SOURCE_LINE.search(ledger_text.stdout)
    if not match:
        return {"ok": False, "reason": f"{ledger} names no spec on a 'Source: <path>' line, so there's nothing to "
                                       f"snapshot; write the spec to .atdd/{args.area}/spec.md yourself."}
    spec_path = match.group(1)
    blob = subprocess.run(["git", "-C", str(root), "show", f"{args.source}:{spec_path}"], capture_output=True)
    if blob.returncode != 0:
        return {"ok": False, "reason": f"The ledger names {spec_path} as its source, but {args.source} has no such file."}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(blob.stdout)
    heading = re.search(r"^#\s+(.+?)\s*$", blob.stdout.decode("utf-8", errors="replace"), re.MULTILINE)
    issue = {"ref": args.area, "key": args.area, "title": heading.group(1) if heading else args.area,
             "source": f"{args.source}:{spec_path}"}
    (target.parent / "issue.json").write_text(json.dumps(issue) + "\n", encoding="utf-8")
    return {"ok": True, "specPath": f".atdd/{args.area}/spec.md", "written": True, "source": issue["source"],
            "title": issue["title"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    planning = commands.add_parser("plan")
    planning.add_argument("--source", required=True)
    planning.add_argument("--area", action="append")
    planning.add_argument("--unit", action="append")
    planning.add_argument("--open-prs", action="store_true")
    planning.add_argument("--summary", action="store_true", help="print only the per-area summary")
    snapping = commands.add_parser("snapshot")
    snapping.add_argument("--source", required=True)
    snapping.add_argument("--area", required=True)
    args = parser.parse_args()
    result = plan(args) if args.command == "plan" else snapshot(args)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
