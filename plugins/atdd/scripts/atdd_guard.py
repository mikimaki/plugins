#!/usr/bin/env python3
"""PreToolUse guard for the atdd plugin's agents.

    atdd_guard.py edit   # Edit / Write / NotebookEdit
    atdd_guard.py git    # Bash commands that invoke git

Does nothing for the main session, for other agents, and in repos without .claude/atdd.json.
For atdd agents it enforces who may write where and which git commands they may run, and
it gates the implementer's commits on locked files and the repo's fast checks.
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
PREPARER = "atdd:preparer"
GHERKIN_WRITER = "atdd:gherkin-writer"
READ_ONLY = {"atdd:gherkin-reviewer", "atdd:reviewer", "atdd:qa", "atdd:quality-tester", GHERKIN_WRITER}

GIT_READ_COMMANDS = {
    "status", "diff", "log", "show", "blame", "rev-parse", "ls-files", "grep", "merge-base", "describe",
    "shortlog", "cat-file", "ls-tree", "name-rev", "check-ignore", "rev-list", "for-each-ref", "show-ref",
    "reflog", "help", "version", "count-objects", "var", "whatchanged", "range-diff", "annotate",
}
GIT_GLOBAL_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}
IMPLEMENTER_FORBIDDEN = {
    "push": "Pushing is the human's call once the pipeline is done.",
    "rebase": "Rewriting history breaks the pipeline's diffs.",
    "merge": "Merging other branches isn't part of implementing this issue.",
    "pull": "Pulling changes the base under the pipeline.",
    "cherry-pick": "Bring code in by writing it for this issue.",
    "switch": "Stay on the issue branch.",
    "filter-branch": "Rewriting history breaks the pipeline's diffs.",
    "update-ref": "Moving refs by hand breaks the pipeline's diffs.",
    "clean": "git clean deletes untracked files irreversibly.",
}
BRANCH_WRITE_FLAGS = {"-d", "-D", "-m", "-M", "-c", "-C", "-f", "--force", "--delete", "--move", "--copy",
                      "-u", "--set-upstream-to", "--unset-upstream", "--edit-description"}
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

    if agent == IMPLEMENTER:
        if is_locked(path, config):
            return deny(
                f"{path} is locked (lockedPaths in .claude/atdd.json): the approved scenarios, the requirements "
                "ledger, the quality-gate scripts and threshold configs belong to humans. If one of them is wrong, "
                "stop and report blocked with your reasoning."
            )
        return allow()

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


def run_fast_checks(root: Path, config: dict) -> int:
    command = (config.get("commands") or {}).get("fastChecks")
    if not command:
        return deny("commands.fastChecks is missing from .claude/atdd.json, so the commit can't be checked. Report blocked; a human must run /atdd:init.")
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


def check_implementer_git(invocations: list[list[str]], root: Path, config: dict) -> int:
    commits = False
    for subcommand, *rest in invocations:
        if subcommand in IMPLEMENTER_FORBIDDEN:
            return deny(f"git {subcommand} isn't allowed for the implementer. {IMPLEMENTER_FORBIDDEN[subcommand]}")
        if subcommand == "reset" and any(flag in rest for flag in ("--hard", "--soft", "--keep", "--merge")):
            return deny("git reset with --hard/--soft/--keep/--merge isn't allowed for the implementer; make a new commit that undoes what you need to undo.")
        if subcommand == "checkout" and any(flag in rest for flag in ("-b", "-B", "--orphan", "--detach")):
            return deny("Stay on the issue branch; creating or detaching branches isn't allowed for the implementer.")
        if subcommand == "branch" and (set(rest) & BRANCH_WRITE_FLAGS or (rest and not rest[0].startswith("-"))):
            return deny("Creating, renaming or deleting branches isn't allowed for the implementer.")
        if subcommand == "commit":
            if "--amend" in rest:
                return deny("Don't amend: the pipeline tracks each round's commit. Make a new commit instead.")
            commits = True
    if not commits:
        return allow()

    locked = [p for p in changed_paths(root) if is_locked(p, config)]
    if locked:
        listing = "\n".join(f"  {p}" for p in locked)
        return deny(
            "Commit refused: these locked files (lockedPaths in .claude/atdd.json) are modified. Revert them "
            f"(git restore <path>, or delete new files there) and commit again:\n{listing}\n"
            "If one of them really is wrong, stop and report blocked with your reasoning instead."
        )
    return run_fast_checks(root, config)


def check_preparer_git(invocations: list[list[str]], root: Path, config: dict) -> int:
    features = str(config.get("featuresDir", "")).strip("/")
    for subcommand, *rest in invocations:
        if is_read_only_git(subcommand, rest) or subcommand in {"switch", "add"}:
            continue
        if subcommand == "commit":
            outside = [p for p in changed_paths(root) if not (features and p.startswith(features + "/"))]
            staged = [p for p in git(root, "diff", "--cached", "--name-only").splitlines() if p in outside]
            if staged:
                return deny("The preparer only commits the feature directory; unstage these first:\n" + "\n".join(staged))
            continue
        return deny(f"git {subcommand} isn't part of the preparer's job.")
    return allow()


def is_read_only_git(subcommand: str, rest: list) -> bool:
    if subcommand in GIT_READ_COMMANDS:
        return True
    if subcommand == "branch":
        return not (set(rest) & BRANCH_WRITE_FLAGS) and (not rest or rest[0].startswith("-"))
    if subcommand == "worktree":
        return rest[:1] == ["list"]
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
    if agent == IMPLEMENTER:
        return check_implementer_git(invocations, root, config)
    if agent == PREPARER:
        return check_preparer_git(invocations, root, config)
    if agent in READ_ONLY:
        return check_read_only_git(agent, invocations)
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
    return allow()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # a broken guard must not wedge the session
        print(f"atdd guard error (allowing the action): {error}", file=sys.stderr)
        sys.exit(1)
