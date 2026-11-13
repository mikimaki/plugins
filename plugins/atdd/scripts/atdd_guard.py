#!/usr/bin/env python3
"""PreToolUse guard for the atdd plugin's agents.

    atdd_guard.py edit   # Edit / Write / NotebookEdit
    atdd_guard.py git    # Bash commands that invoke git
    atdd_guard.py gh     # Bash commands that invoke the GitHub CLI

Does nothing for the main session, for other agents, and in repos without .claude/atdd.json.
For atdd agents it enforces who may write where and which git commands they may run, and
it gates the implementer's commits on locked files and the repo's fast checks. The
acceptance-test-coverer (/atdd:cover) writes only inside the test paths, and its commits are
gated on locked files, on nothing outside the test paths having changed, and on the cover
fast checks. The preparer commits only test paths, and pushes only the unit branches
/atdd:cover-queue created, never the base or integration branch, and never by force. Only the
preparer may open a pull request; every other gh command an atdd agent runs must only read.
"""

from __future__ import annotations

import fnmatch
import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

IMPLEMENTER = "atdd:implementer"
COVERER = "atdd:acceptance-test-coverer"
PREPARER = "atdd:preparer"
GHERKIN_WRITER = "atdd:gherkin-writer"
READ_ONLY = {"atdd:gherkin-reviewer", "atdd:reviewer", "atdd:qa", "atdd:quality-tester", GHERKIN_WRITER,
             "atdd:coverage-reviewer", "atdd:coverage-quality-tester"}
COMMITTERS = {IMPLEMENTER: "the implementer", COVERER: "the acceptance-test-coverer"}
DEFAULT_COVER_FAST_CHECKS = "python3 scripts/atdd/gate.py cover-fast"

GIT_READ_COMMANDS = {
    "status", "diff", "log", "show", "blame", "rev-parse", "ls-files", "grep", "merge-base", "describe",
    "shortlog", "cat-file", "ls-tree", "name-rev", "check-ignore", "rev-list", "for-each-ref", "show-ref",
    "reflog", "help", "version", "count-objects", "var", "whatchanged", "range-diff", "annotate", "ls-remote",
}
GIT_GLOBAL_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}
COMMITTER_FORBIDDEN = {
    "push": "Pushing is the human's call once the pipeline is done.",
    "rebase": "Rewriting history breaks the pipeline's diffs.",
    "merge": "Merging other branches isn't part of this issue's work.",
    "pull": "Pulling changes the base under the pipeline.",
    "cherry-pick": "Bring code in by writing it for this issue.",
    "switch": "Stay on the issue branch.",
    "filter-branch": "Rewriting history breaks the pipeline's diffs.",
    "update-ref": "Moving refs by hand breaks the pipeline's diffs.",
    "clean": "git clean deletes untracked files irreversibly.",
}
BRANCH_WRITE_FLAGS = {"-d", "-D", "-m", "-M", "-c", "-C", "-f", "--force", "--delete", "--move", "--copy",
                      "-u", "--set-upstream-to", "--unset-upstream", "--edit-description"}
PUSH_FORBIDDEN_FLAGS = {"-f", "--force", "--force-with-lease", "--force-if-includes", "-d", "--delete", "--mirror",
                        "--all", "--branches", "--tags", "--prune", "--follow-tags", "-o", "--push-option"}
COMMIT_OPTIONS_WITH_VALUE = {"-m", "--message", "-F", "--file", "-C", "--reuse-message", "-c", "--reedit-message",
                             "--author", "--date", "--fixup", "--squash", "--cleanup", "-t", "--template", "--trailer"}
STACK_KEY = re.compile(r"^branch\..+\.atdd[A-Za-z]+$")
DEFAULT_INTEGRATION_BRANCH = "atdd/cover-base"
GH_READ_VERBS = {"view", "list", "status", "diff", "checks"}
FAST_CHECK_TIMEOUT_SECONDS = 1750
SUMMARY_LINES = 40


def allow() -> int:
    return 0


def deny(reason: str) -> int:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    return 0


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def repo_root(cwd: str) -> Path | None:
    try:
        return Path(git(Path(cwd), "rev-parse", "--show-toplevel").strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def load_config(root: Path) -> dict | None:
    path = root / ".claude" / "atdd.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def matches(path: str, pattern: str) -> bool:
    pattern = pattern.strip()
    if pattern.startswith("./"):
        pattern = pattern[2:]
    if pattern.endswith("/"):
        pattern += "**"
    candidates = {pattern}
    if pattern.startswith("**/"):
        candidates.add(pattern[3:])
    if pattern.endswith("/**"):
        candidates.add(pattern[:-3])
    return any(fnmatch.fnmatchcase(path, p) for p in candidates)


def is_locked(path: str, config: dict) -> bool:
    return any(matches(path, p) for p in config.get("lockedPaths", []))


def cover_test_paths(config: dict) -> list[str]:
    """Globs for the files a cover run may change: cover.testPaths, else every gate.testDirs folder plus featuresDir.
    The quality gate (skills/quality-gate/scripts/gate.py) resolves them the same way."""
    configured = (config.get("cover") or {}).get("testPaths")
    if configured:
        return [str(p).strip() for p in configured if str(p).strip()]
    folders = list((config.get("gate") or {}).get("testDirs", [])) + [config.get("featuresDir", "")]
    return sorted({f"{f}/**" for f in (str(d).strip().strip("/") for d in folders) if f and f != "."})


def in_test_paths(path: str, config: dict) -> bool:
    return any(matches(path, p) for p in cover_test_paths(config))


def relative_to_root(raw: str, cwd: str, root: Path) -> str | None:
    target = Path(raw)
    if not target.is_absolute():
        target = Path(cwd) / target
    try:
        return target.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def check_edit(agent: str, data: dict, root: Path, config: dict) -> int:
    tool_input = data.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw:
        return allow()
    path = relative_to_root(raw, data.get("cwd", str(root)), root)
    if path is None:
        return allow()
    in_workspace = path == ".atdd" or path.startswith(".atdd/")

    if agent in COMMITTERS and is_locked(path, config):
        return deny(
            f"{path} is locked (lockedPaths in .claude/atdd.json): the approved scenarios, the requirements "
            "ledger, the quality-gate scripts and threshold configs belong to humans. If one of them is wrong, "
            "stop and report blocked with your reasoning."
        )
    if agent == IMPLEMENTER:
        return allow()

    if agent == COVERER:
        if in_workspace or in_test_paths(path, config):
            return allow()
        return deny(
            f"{path} is outside the test paths ({', '.join(cover_test_paths(config)) or 'none configured'}). "
            "/atdd:cover only adds acceptance tests: production code, configuration, build files and manifests stay "
            "exactly as they are. If covering a scenario needs a change there (a seam, a package), stop and report "
            "blocked, naming what a human would have to change."
        )

    if agent == GHERKIN_WRITER:
        features = str(config.get("featuresDir", "")).strip("/")
        if in_workspace or (features and (path == features or path.startswith(features + "/"))):
            return allow()
        return deny(f"The gherkin-writer only writes inside {features or 'the features directory'}/ and .atdd/; {path} is outside both.")

    if in_workspace or (agent == PREPARER and path == ".git/info/exclude"):
        return allow()
    return deny(f"{agent} doesn't modify the repository; write notes and evidence under .atdd/ instead of {path}.")


def git_invocations(command: str) -> list[list[str]]:
    """Return [subcommand, *args] for every git invocation in a shell command."""
    invocations = []
    for segment in re.split(r"\|\||&&|[;|&\n()`]|\$\(", command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()
        while tokens and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]) or tokens[0] in {"sudo", "env", "command", "time", "nice", "nohup", "exec"}):
            tokens = tokens[1:]
        if not tokens or Path(tokens[0]).name != "git":
            continue
        rest = tokens[1:]
        while rest and rest[0].startswith("-"):
            option = rest.pop(0)
            if option in GIT_GLOBAL_OPTS_WITH_VALUE and rest:
                rest.pop(0)
        if rest:
            invocations.append(rest)
    return invocations


def changed_paths(root: Path) -> list[str]:
    out = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    entries = out.split("\0")
    paths, i = [], 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        paths.append(path)
        if "R" in status or "C" in status:
            if i < len(entries) and entries[i]:
                paths.append(entries[i])
            i += 1
    return paths


def run_fast_checks(root: Path, command: str) -> int:
    log_dir = root / ".atdd" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"fast-checks-{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}.log"
    try:
        with log_path.open("w", encoding="utf-8") as log:
            result = subprocess.run(["bash", "-c", command], cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=FAST_CHECK_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return deny(f"The fast checks ({command}) ran longer than {FAST_CHECK_TIMEOUT_SECONDS}s and were stopped. Log: {log_path.relative_to(root)}")
    if result.returncode == 0:
        return allow()
    tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-SUMMARY_LINES:]
    return deny(
        f"Commit refused: the fast checks failed (exit {result.returncode}). Fix the causes and commit again.\n"
        f"Full log: {log_path.relative_to(root)}\n--- last lines ---\n" + "\n".join(tail)
    )


def check_committer_git(agent: str, invocations: list[list[str]], root: Path, config: dict) -> int:
    role = COMMITTERS[agent]
    commits = False
    for subcommand, *rest in invocations:
        if subcommand in COMMITTER_FORBIDDEN:
            return deny(f"git {subcommand} isn't allowed for {role}. {COMMITTER_FORBIDDEN[subcommand]}")
        if subcommand == "reset" and any(flag in rest for flag in ("--hard", "--soft", "--keep", "--merge")):
            return deny(f"git reset with --hard/--soft/--keep/--merge isn't allowed for {role}; make a new commit that undoes what you need to undo.")
        if subcommand == "checkout" and any(flag in rest for flag in ("-b", "-B", "--orphan", "--detach")):
            return deny(f"Stay on the issue branch; creating or detaching branches isn't allowed for {role}.")
        if subcommand == "branch" and (set(rest) & BRANCH_WRITE_FLAGS or (rest and not rest[0].startswith("-"))):
            return deny(f"Creating, renaming or deleting branches isn't allowed for {role}.")
        if subcommand == "commit":
            if "--amend" in rest:
                return deny("Don't amend: the pipeline tracks each round's commit. Make a new commit instead.")
            commits = True
    if not commits:
        return allow()

    dirty = changed_paths(root)
    locked = [p for p in dirty if is_locked(p, config)]
    if locked:
        listing = "\n".join(f"  {p}" for p in locked)
        return deny(
            "Commit refused: these locked files (lockedPaths in .claude/atdd.json) are modified. Revert them "
            f"(git restore <path>, or delete new files there) and commit again:\n{listing}\n"
            "If one of them really is wrong, stop and report blocked with your reasoning instead."
        )
    commands = config.get("commands") or {}
    if agent == COVERER:
        outside = [p for p in dirty if not in_test_paths(p, config) and not p.startswith(".atdd/")]
        if outside:
            listing = "\n".join(f"  {p}" for p in outside)
            return deny(
                "Commit refused: /atdd:cover changes nothing but tests, and these files outside the test paths "
                f"({', '.join(cover_test_paths(config)) or 'none configured'}) are modified or new. Revert them "
                f"(git restore <path>; delete files you created) and commit again:\n{listing}\n"
                "If covering a scenario really needs one of them changed, stop and report blocked instead."
            )
        return run_fast_checks(root, commands.get("coverFastChecks") or DEFAULT_COVER_FAST_CHECKS)
    if not commands.get("fastChecks"):
        return deny("commands.fastChecks is missing from .claude/atdd.json, so the commit can't be checked. Report blocked; a human must run /atdd:init.")
    return run_fast_checks(root, commands["fastChecks"])


def in_features(path: str, config: dict) -> bool:
    features = str(config.get("featuresDir", "")).strip("/")
    return bool(features) and (path == features or path.startswith(features + "/"))


def preparer_may_commit(path: str, config: dict) -> bool:
    """The feature directory (scenarios) and, when a cover-queue unit is parked, the test paths: never anything else."""
    return in_features(path, config) or in_test_paths(path, config)


def git_config_value(root: Path, key: str) -> str:
    result = subprocess.run(["git", "-C", str(root), "config", "--get", key], capture_output=True, text=True)
    return result.stdout.strip()


def protected_branches(config: dict) -> set:
    base = str(config.get("baseBranch") or "main")
    integration = str((config.get("cover") or {}).get("integrationBranch") or DEFAULT_INTEGRATION_BRANCH)
    return {base, integration, "main", "master"}


def is_queue_unit_branch(root: Path, branch: str, config: dict) -> bool:
    """A branch /atdd:cover-queue created: the preparer records its stack parent as branch.<name>.atddBase."""
    return bool(branch) and branch not in protected_branches(config) and bool(git_config_value(root, f"branch.{branch}.atddBase"))


def check_preparer_commit(rest: list, root: Path, config: dict) -> str | None:
    if "--amend" in rest:
        return "Don't amend: make a new commit."
    pathspecs, i = [], 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--":
            pathspecs += rest[i + 1:]
            break
        if arg in COMMIT_OPTIONS_WITH_VALUE:
            i += 2
            continue
        if not arg.startswith("-"):
            pathspecs.append(arg)
        i += 1
    candidates = set(git(root, "diff", "--cached", "--name-only").splitlines())
    if any(flag in rest for flag in ("-a", "--all")) or any(re.fullmatch(r"-[A-Za-z]*a[A-Za-z]*", a) for a in rest if not a.startswith("--")):
        candidates |= set(git(root, "diff", "--name-only").splitlines())
    if pathspecs:
        listed = git(root, "status", "--porcelain=v1", "--untracked-files=all", "--", *pathspecs).splitlines()
        candidates |= {line[3:].split(" -> ")[-1] for line in listed if len(line) > 3}
    outside = sorted(p for p in candidates if p and not preparer_may_commit(p, config))
    if outside:
        return ("The preparer only commits the feature directory and the test paths "
                    f"({', '.join(cover_test_paths(config)) or 'none configured'}); leave these out:\n" + "\n".join(outside))
    return None


def check_preparer_checkout(rest: list, config: dict) -> str | None:
    if "--" not in rest:
        return ("Use git switch to change branches. The preparer uses git checkout only as "
                    "`git checkout <ref> -- <paths>`, to bring scenario files in from the source ref.")
    paths = rest[rest.index("--") + 1:]
    options = [a for a in rest[:rest.index("--")] if a.startswith("-")]
    outside = [p for p in paths if not in_features(p.rstrip("/"), config)]
    if options or not paths or outside:
        return ("git checkout <ref> -- <paths> may only bring in files inside the feature directory "
                    f"({config.get('featuresDir', '')}/); the spec stays out of the branch." + (
                        "\nOutside: " + ", ".join(outside) if outside else ""))
    return None


def check_preparer_branch(rest: list, config: dict) -> str | None:
    names = [a for a in rest if not a.startswith("-")]
    flags = [a for a in rest if a.startswith("-")]
    if set(flags) - {"--no-track", "--track"} or not 1 <= len(names) <= 2:
        return "The preparer only creates branches (git branch <name> <start>); it never renames, moves or deletes them."
    if names[0] in protected_branches(config) - {str((config.get("cover") or {}).get("integrationBranch") or DEFAULT_INTEGRATION_BRANCH)}:
        return f"{names[0]} is the base branch; the preparer doesn't create or move it."
    return None


def check_preparer_config(rest: list) -> str | None:
    reading = {"--get", "--get-all", "--list", "-l", "--get-regexp"}
    if rest[:1] and rest[0] in reading:
        return None
    args = [a for a in rest if a not in ("--unset", "--unset-all", "--local", "--replace-all")]
    if args and STACK_KEY.match(args[0]) and len(args) <= 2:
        return None
    return "The preparer only sets branch.<branch>.atdd* keys (the queue's records of a unit branch) with git config."


def check_preparer_push(rest: list, root: Path, config: dict) -> str | None:
    flags = [a for a in rest if a.startswith("-")]
    refs = [a for a in rest if not a.startswith("-")]
    if set(flags) & PUSH_FORBIDDEN_FLAGS or set(flags) - {"-u", "--set-upstream", "-q", "--quiet", "--porcelain"}:
        return "Push plainly: git push <remote> <unit-branch>. No force, deletes, tags or other options."
    if len(refs) != 2 or any(ch in refs[1] for ch in ":+*^~") or refs[1] == "HEAD":
        return "Push exactly one unit branch by name: git push <remote> <unit-branch>."
    branch = refs[1][len("refs/heads/"):] if refs[1].startswith("refs/heads/") else refs[1]
    if not is_queue_unit_branch(root, branch, config):
        return (f"{branch} isn't a unit branch /atdd:cover-queue created (no branch.{branch}.atddBase), or it's the base "
                    "or integration branch. The queue pushes only its own unit branches.")
    return None


def check_preparer_rebase(rest: list, root: Path, config: dict) -> str | None:
    if rest == ["--abort"]:
        return None
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
    pushed = any(line.endswith("/" + branch) for line in git(root, "for-each-ref", "--format=%(refname)", "refs/remotes").splitlines())
    simple = (len(rest) == 1 and not rest[0].startswith("-")) or (len(rest) == 3 and rest[0] == "--onto")
    if not simple or not is_queue_unit_branch(root, branch, config) or pushed:
        return ("The preparer rebases only a parked or interrupted unit branch that was never pushed, onto the top of "
                    "the stack (git rebase <top>, or git rebase --onto <top> <old-parent>), and aborts on a conflict.")
    return None


def check_preparer_git(invocations: list[list[str]], root: Path, config: dict) -> int:
    checks = {
        "commit": lambda rest: check_preparer_commit(rest, root, config),
        "checkout": lambda rest: check_preparer_checkout(rest, config),
        "branch": lambda rest: check_preparer_branch(rest, config),
        "config": check_preparer_config,
        "push": lambda rest: check_preparer_push(rest, root, config),
        "rebase": lambda rest: check_preparer_rebase(rest, root, config),
        "fetch": lambda rest: "Fetch without refspecs that write local branches." if any(":" in a for a in rest) else None,
    }
    for subcommand, *rest in invocations:
        if is_read_only_git(subcommand, rest) or subcommand in {"switch", "add"}:
            continue
        if subcommand not in checks:
            return deny(f"git {subcommand} isn't part of the preparer's job.")
        reason = checks[subcommand](rest)
        if reason:
            return deny(reason)
    return allow()


def is_read_only_git(subcommand: str, rest: list) -> bool:
    if subcommand in GIT_READ_COMMANDS:
        return True
    if subcommand == "branch":
        return not (set(rest) & BRANCH_WRITE_FLAGS) and (not rest or rest[0].startswith("-"))
    if subcommand == "worktree":
        return rest[:1] == ["list"]
    if subcommand == "remote":
        return not rest or rest[0] in ("-v", "--verbose", "get-url", "show")
    if subcommand == "config":
        return bool(rest[:1]) and rest[0] in ("--get", "--get-all", "--list", "-l")
    if subcommand == "stash":
        return rest[:1] == ["list"]
    return False


def check_read_only_git(agent: str, invocations: list[list[str]]) -> int:
    for subcommand, *rest in invocations:
        if not is_read_only_git(subcommand, rest):
            return deny(f"{agent} only reads the repository; git {subcommand} would change it. Report what you found instead.")
    return allow()


def check_git(agent: str, data: dict, root: Path, config: dict) -> int:
    command = (data.get("tool_input") or {}).get("command", "")
    invocations = git_invocations(command)
    if not invocations:
        return allow()
    if agent in COMMITTERS:
        return check_committer_git(agent, invocations, root, config)
    if agent == PREPARER:
        return check_preparer_git(invocations, root, config)
    if agent in READ_ONLY:
        return check_read_only_git(agent, invocations)
    return allow()


def gh_invocations(command: str) -> list[list[str]]:
    """Return the arguments of every gh invocation in a shell command."""
    invocations = []
    for segment in re.split(r"\|\||&&|[;|&\n()`]|\$\(", command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()
        while tokens and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]) or tokens[0] in {"env", "command", "time", "nohup", "exec"}):
            tokens = tokens[1:]
        if tokens and Path(tokens[0]).name == "gh":
            invocations.append(tokens[1:])
    return invocations


def option_value(args: list, *names: str) -> str | None:
    for i, arg in enumerate(args):
        for name in names:
            if arg == name and i + 1 < len(args):
                return args[i + 1]
            if name.startswith("--") and arg.startswith(name + "="):
                return arg[len(name) + 1:]
    return None


def gh_denial(agent: str, args: list, root: Path, config: dict) -> str | None:
    if not args or args[0] in ("--version", "version", "help", "--help"):
        return None
    group, verb = args[0], (args[1] if len(args) > 1 else "")
    if (group, verb) == ("auth", "status") or group == "search" or verb in GH_READ_VERBS:
        return None
    if group == "api":
        method = (option_value(args, "-X", "--method") or "GET").upper()
        writes = method != "GET" or any(a in ("-f", "-F", "--field", "--raw-field", "--input") or
                                        a.startswith(("--field=", "--raw-field=", "--input=")) for a in args)
        return "atdd agents only read through gh api (GET without fields)." if writes else None
    if agent == PREPARER and (group, verb) == ("pr", "create"):
        head, base = option_value(args, "-H", "--head"), option_value(args, "-B", "--base")
        if not head or not base:
            return "Open a unit's pull request with both --base <stack parent> and --head <unit branch>."
        if not is_queue_unit_branch(root, head, config):
            return f"{head} isn't a unit branch /atdd:cover-queue created; the preparer opens pull requests only for those."
        return None
    return (f"gh {' '.join(args[:2])} would change something on GitHub. atdd agents only read there; the preparer may "
            "open a unit's pull request with gh pr create.")


def check_gh(agent: str, data: dict, root: Path, config: dict) -> int:
    for args in gh_invocations((data.get("tool_input") or {}).get("command", "")):
        reason = gh_denial(agent, args, root, config)
        if reason:
            return deny(reason)
    return allow()


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    data = json.load(sys.stdin)
    agent = data.get("agent_type") or ""
    if not agent.startswith("atdd:"):
        return allow()
    root = repo_root(data.get("cwd", "."))
    if root is None:
        return allow()
    config = load_config(root)
    if config is None:
        return allow()
    if mode == "edit":
        return check_edit(agent, data, root, config)
    if mode == "git":
        return check_git(agent, data, root, config)
    if mode == "gh":
        return check_gh(agent, data, root, config)
    return allow()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # a broken guard must not wedge the session
        print(f"atdd guard error (allowing the action): {error}", file=sys.stderr)
        sys.exit(1)
