#!/usr/bin/env python3
"""ATDD quality gate: deterministic pass/fail checks over everything changed since the base commit.

    python3 scripts/atdd/gate.py fast    # commit gate: build, format, lint, tests, steps, complexity, NPath, cycles,
                                         # suppressions, duplication, architecture, dead code, locked files
    python3 scripts/atdd/gate.py full    # adds diff coverage, mutation and vulnerable dependencies
    python3 scripts/atdd/gate.py full --only mutation,diff-coverage
    python3 scripts/atdd/gate.py cover-fast   # /atdd:cover's commit gate: only test files changed, and they're sound
    python3 scripts/atdd/gate.py cover-full   # adds duplication, locked files, and the acceptance tests' own coverage and
                                              # mutation score on the production files they protect
    python3 scripts/atdd/gate.py --version

/atdd:init installs this script into each repo as scripts/atdd/gate.py, a locked file. Its source is
skills/quality-gate/scripts/gate.py in the atdd plugin; to update a repo, copy that over and commit it.

Configuration lives in .claude/atdd.json ("stack", "baseBranch", "featuresDir", "lockedPaths", "gate").
Environment overrides: ATDD_BASE (diff base, default: see resolve_base), ATDD_OUT (report
directory, default .atdd/quality/latest), ATDD_SCENARIOS_COMMIT (commit holding the approved scenarios,
for the locked-files check), ATDD_COVER_TARGETS (cover-full: a file listing, one per line, the production files the
acceptance tests protect). Writes <out>/report.json plus one log per check; exits 1 if any check fails.

The cover modes judge a change that adds acceptance tests to existing code and must leave everything else alone, so
they measure the test code instead of production code, never look at architecture or dependency cycles, and fail when
any file outside the test paths (cover.testPaths, else gate.testDirs plus featuresDir) differs from the base, or
when a step text has more than one step definition.

Standard library only; Python 3.9+. The tools it drives (lizard, diff-cover, jscpd, Stryker, ...) are
installed per repo, see the atdd quality-gate skill. Python NPath and Python import cycles are computed here
with the ast module; C# uses the Roslyn helper in scripts/atdd/dotnet-quality; JS/TS uses ESLint and
dependency-cruiser.
"""

from __future__ import annotations

import argparse
import ast
import csv
import fnmatch
import glob
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

GATE_VERSION = "0.4.0"
JSCPD = "jscpd@5.4.2"

DEFAULT_THRESHOLDS = {
    "diffCoverage": 100,
    "diffBranchCoverage": 100,
    "mutationScore": 80,
    "cyclomatic": 10,
    "cognitive": 15,
    "npath": 200,
    "functionLength": 60,
    "parameters": 5,
}

# Duplication, architecture, dead-code and locked-files each took about a second or less on the TypeScript, Python
# and .NET fixtures, so the commit gate runs them too. The full-only checks are the slow ones.
FAST_CHECKS = ["build", "format", "lint", "tests", "undefined-steps", "complexity", "cognitive", "npath",
               "dependency-cycles", "suppressions", "duplication", "architecture", "dead-code", "locked-files"]
FULL_CHECKS = FAST_CHECKS + ["diff-coverage", "mutation", "vulnerabilities"]
COVER_FAST_CHECKS = ["production-untouched", "build", "format", "lint", "tests", "undefined-steps", "duplicate-steps",
                     "complexity", "cognitive", "npath", "suppressions"]
COVER_FULL_CHECKS = COVER_FAST_CHECKS + ["duplication", "locked-files", "acceptance-coverage", "acceptance-mutation"]
# mode -> (the key in .claude/atdd.json "gate" that can replace its checks, its default checks)
MODES = {
    "fast": ("fast", FAST_CHECKS),
    "full": ("full", FULL_CHECKS),
    "cover-fast": ("coverFast", COVER_FAST_CHECKS),
    "cover-full": ("coverFull", COVER_FULL_CHECKS),
}

STACKS = {
    "dotnet": {
        "extensions": (".cs",),
        "jscpd_format": "csharp",
        "test_patterns": ["*Tests/*", "*Tests.cs", "*Test.cs", "*.Tests.*/*", "*/tests/*", "tests/*", "test/*", "*/test/*"],
        "generated_patterns": ["scripts/atdd/*", "*.feature.cs", "*.g.cs", "*.g.i.cs", "*.Designer.cs", "*/obj/*", "*/bin/*",
                               "obj/*", "bin/*", "*/Migrations/*"],
        "suppressions": [
            r"#pragma\s+warning\s+disable", r"SuppressMessage", r"\[ExcludeFromCodeCoverage",
            r"Stryker\s+disable", r"\bSkip\s*=", r"\[Ignore\b", r"#nullable\s+disable", r"ReSharper\s+disable",
            r"\[Explicit\b",
        ],
        "manifests": ["*.csproj", "Directory.Packages.props", "Directory.Build.props", "packages.lock.json", "*.fsproj"],
    },
    "typescript": {
        "extensions": (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"),
        "jscpd_format": "typescript,javascript,tsx,jsx",
        "test_patterns": ["*.test.*", "*.spec.*", "*/__tests__/*", "test/*", "tests/*", "*/test/*", "*/tests/*", "e2e/*"],
        "generated_patterns": ["scripts/atdd/*", "*.d.ts", "dist/*", "build/*", "coverage/*", "*/node_modules/*", "node_modules/*",
                               ".features-gen/*", "*/.features-gen/*"],
        "suppressions": [
            r"eslint-disable", r"@ts-ignore", r"@ts-nocheck", r"@ts-expect-error", r"Stryker\s+disable",
            r"\.skip\(", r"\bxit\(", r"\bxdescribe\(", r"\.only\(", r"istanbul\s+ignore", r"c8\s+ignore",
            r"v8\s+ignore", r"biome-ignore",
        ],
        "manifests": ["package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"],
    },
    "python": {
        "extensions": (".py",),
        "jscpd_format": "python",
        "test_patterns": ["test_*.py", "*_test.py", "*/test_*.py", "*/tests/*", "tests/*", "conftest.py", "*/conftest.py"],
        "generated_patterns": ["scripts/atdd/*", "mutants/*", "*/migrations/*", "*_pb2.py", "*_pb2_grpc.py", "build/*", "dist/*", ".venv/*", "*/.venv/*"],
        "suppressions": [
            r"#\s*noqa", r"#\s*type:\s*ignore", r"#\s*pragma:\s*no\s*(cover|mutate)", r"pytest\.mark\.skip",
            r"pytest\.mark\.xfail", r"unittest\.skip", r"#\s*pylint:\s*disable", r"#\s*nosec", r"#\s*fmt:\s*off",
            r"#\s*pyright:\s*ignore",
        ],
        "manifests": ["pyproject.toml", "requirements*.txt", "poetry.lock", "uv.lock", "Pipfile*", "setup.py", "setup.cfg"],
    },
}
STACKS["nextjs"] = {
    **STACKS["typescript"],
    "test_patterns": STACKS["typescript"]["test_patterns"] + ["playwright/*", "*/playwright/*"],
    "generated_patterns": STACKS["typescript"]["generated_patterns"] + [
        ".next/*", "*/.next/*", "next-env.d.ts", "generated/prisma/*", "*/generated/prisma/*", "playwright-report/*",
        "test-results/*", "reports/*", "coverage/*"],
    "suppressions": STACKS["typescript"]["suppressions"] + [
        r"\.fixme\(", r"\btest\.fail\(", r"\.skipIf\(", r"\.runIf\(", r"\.todo\("],
    "manifests": STACKS["typescript"]["manifests"] + ["bun.lock", "bun.lockb"],
}
NODE_STACKS = ("typescript", "nextjs")


@dataclass
class Result:
    name: str
    status: str = "pass"          # pass | fail | flaky | warn | skipped
    detail: str = ""
    metric: str = ""
    threshold: str = ""
    log: str = ""
    seconds: float = 0.0


@dataclass
class Gate:
    root: Path
    config: dict
    mode: str
    base: str
    out: Path
    scenarios_commit: str | None
    stack: str = ""
    profile: dict = field(default_factory=dict)
    gate_config: dict = field(default_factory=dict)
    thresholds: dict = field(default_factory=dict)
    changed: list = field(default_factory=list)
    added_lines: dict = field(default_factory=dict)
    coverage_reports: list = field(default_factory=list)
    coverlet_reports: list = field(default_factory=list)
    branches_measured: bool = False
    cover_targets_file: str | None = None
    flaky_tests: list = field(default_factory=list)
    _functions: dict | None = None

    # ---------- helpers ----------

    @property
    def node(self) -> bool:
        return self.stack in NODE_STACKS

    @property
    def cover(self) -> bool:
        """The cover modes judge acceptance tests added to existing code: the test code is what gets measured."""
        return self.mode.startswith("cover")

    @property
    def subject(self) -> str:
        return "test code" if self.cover else "production code"

    def log_path(self, name: str) -> Path:
        return self.out / "logs" / f"{name}.log"

    def rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def run(self, name: str, command, log_name: str | None = None, env: dict | None = None,
            append: bool = False, timeout: int | None = None) -> int:
        """Run a command from the repo root, logging to logs/<log_name or name>.log. Returns the exit code."""
        log = self.log_path(log_name or name)
        log.parent.mkdir(parents=True, exist_ok=True)
        shell = isinstance(command, str)
        printable = command if shell else " ".join(shlex.quote(c) for c in command)
        merged_env = {**os.environ, **(env or {})}
        with log.open("a" if append else "w", encoding="utf-8") as handle:
            handle.write(f"$ {printable}\n")
            handle.flush()
            try:
                completed = subprocess.run(command, cwd=self.root, shell=shell, stdout=handle,
                                           stderr=subprocess.STDOUT, env=merged_env, timeout=timeout,
                                           executable="/bin/bash" if shell else None)
                code = completed.returncode
            except FileNotFoundError as error:
                handle.write(f"\n{error}\n")
                code = 127
            except subprocess.TimeoutExpired:
                handle.write(f"\nTimed out after {timeout}s\n")
                code = 124
            handle.write(f"\n[exit {code}]\n")
        return code

    def command_override(self, key: str) -> str | None:
        return (self.gate_config.get("commands") or {}).get(key)

    def configured_command(self, key: str) -> str | None:
        return (self.config.get("commands") or {}).get(key)

    def has_tool(self, executable: str) -> bool:
        return shutil.which(executable) is not None

    def matches_any(self, path: str, patterns) -> bool:
        return any(fnmatch.fnmatchcase(path, p) for p in patterns)

    def is_generated(self, path: str) -> bool:
        patterns = list(self.profile["generated_patterns"]) + list(self.gate_config.get("exclude", []))
        return self.matches_any(path, patterns)

    def is_test(self, path: str) -> bool:
        patterns = list(self.profile["test_patterns"]) + [f"{d.strip('/')}/*" for d in self.gate_config.get("testDirs", [])]
        features = str(self.config.get("featuresDir", "")).strip("/")
        if features:
            patterns.append(f"{features}/*")
        return self.matches_any(path, patterns)

    def in_source_dirs(self, path: str) -> bool:
        dirs = [d.strip("/") for d in self.gate_config.get("sourceDirs", [])]
        return not dirs or any(path == d or path.startswith(d + "/") for d in dirs)

    def code_files(self, include_tests: bool = False) -> list:
        files = [p for p in self.changed if p.endswith(self.profile["extensions"]) and not self.is_generated(p)]
        if self.node:
            files = [p for p in files if not p.endswith(".d.ts")]
        if include_tests:
            return files
        return [p for p in files if not self.is_test(p) and self.in_source_dirs(p)]

    def subject_files(self) -> list:
        """Changed code whose shape the gate judges: production code, or in the cover modes the tests being added."""
        return self.code_files(include_tests=self.cover)

    def in_test_paths(self, path: str) -> bool:
        return any(path_matches(path, pattern) for pattern in cover_test_paths(self.config))

    def targets(self) -> tuple:
        """The production files a cover run protects, listed in ATDD_COVER_TARGETS: (paths, None) or (None, problem)."""
        if not self.cover_targets_file:
            return None, ("No coverage targets: set ATDD_COVER_TARGETS to a file listing, one per line, the production "
                          "files that implement the behavior the scenarios describe.")
        listing = Path(self.cover_targets_file)
        listing = listing if listing.is_absolute() else self.root / listing
        if not listing.is_file():
            return None, f"The coverage targets file {self.rel(listing)} doesn't exist."
        entries = [line.strip() for line in listing.read_text(encoding="utf-8").splitlines()]
        targets = sorted({e[2:] if e.startswith("./") else e for e in entries if e and not e.startswith("#")})
        if not targets:
            return None, f"{self.rel(listing)} lists no files."
        problems = []
        missing = [t for t in targets if not (self.root / t).is_file()]
        if missing:
            problems.append("These coverage targets don't exist:\n" + "\n".join(missing))
        tests = [t for t in targets if t not in missing and (self.in_test_paths(t) or self.is_test(t))]
        if tests:
            problems.append("Coverage targets are production files, never tests:\n" + "\n".join(tests))
        return (None, "\n".join(problems)) if problems else (targets, None)

    def production_files(self) -> list:
        """Every production code file in sourceDirs, changed or not: tracked plus untracked, minus git-ignored."""
        dirs = [d.strip("/") for d in self.gate_config.get("sourceDirs", [])] or ["."]
        listed = git(self.root, "ls-files", "-co", "--exclude-standard", "--", *dirs).splitlines()
        return sorted(p for p in set(listed) if p.endswith(self.profile["extensions"]) and (self.root / p).is_file()
                      and not self.is_generated(p) and not self.is_test(p) and not (self.node and p.endswith(".d.ts")))

    def touched_line(self, path: str, line: int) -> bool:
        return line in self.added_lines.get(path, {})

    def touched(self, path: str, line: int, end: int | None = None) -> bool:
        """True if lines [line, end] were added or changed, or the line sits in a function this change touched."""
        added = self.added_lines.get(path, {})
        if not added:
            return False
        if end is not None:
            return any(line <= n <= end for n in added)
        if line in added:
            return True
        enclosing = [fn for fn in self.functions().get(path, []) if fn["start"] <= line <= fn["end"]]
        if not enclosing:
            return False
        innermost = min(enclosing, key=lambda fn: fn["end"] - fn["start"])
        return any(innermost["start"] <= n <= innermost["end"] for n in added)

    def is_new_file(self, path: str) -> bool:
        return subprocess.run(["git", "-C", str(self.root), "cat-file", "-e", f"{self.base}:{path}"],
                              capture_output=True).returncode != 0

    def functions(self) -> dict:
        """Function ranges in changed code files, from lizard: {file: [{name, start, end, nloc, ccn, params}]}."""
        if self._functions is not None:
            return self._functions
        self._functions = {}
        files = self.code_files(include_tests=True)
        if not files or not self.has_tool("lizard"):
            return self._functions
        listing = self.out / "lizard-files.txt"
        listing.write_text("\n".join(files) + "\n", encoding="utf-8")
        output = subprocess.run(["lizard", "--csv", "-f", str(listing)], cwd=self.root, capture_output=True, text=True).stdout
        for row in csv.reader(output.splitlines()):
            if len(row) < 11 or not row[0].isdigit():
                continue
            nloc, ccn, _tokens, params, _length, _location, path, name, _long_name, start, end = row[:11]
            path = path.replace("\\", "/")
            path = path[2:] if path.startswith("./") else path
            self._functions.setdefault(path, []).append(
                {"name": name, "start": int(start), "end": int(end), "nloc": int(nloc), "ccn": int(ccn), "params": int(params)})
        return self._functions

    def tail(self, log: Path, lines: int = 15) -> str:
        if not log.exists():
            return ""
        return "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


# ---------- git ----------

def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def git_ok(root: Path, *args: str) -> bool:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True).returncode == 0


def commit_of(root: Path, ref: str) -> str | None:
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def merge_base_with(root: Path, name: str) -> tuple:
    """(merge-base of HEAD with branch `name`, note). Of the local branch and origin/<name>, uses the one that contains
    the other, so a local branch that is behind origin doesn't count already-merged changes as part of this one."""
    local, remote = commit_of(root, f"refs/heads/{name}"), commit_of(root, f"refs/remotes/origin/{name}")
    if not local and not remote:
        other = commit_of(root, name)  # a commit, tag or other ref
        return (git(root, "merge-base", other, "HEAD").strip(), "") if other else (None, "")
    if not (local and remote) or local == remote:
        return git(root, "merge-base", local or remote, "HEAD").strip(), ""
    if git_ok(root, "merge-base", "--is-ancestor", local, remote):
        return git(root, "merge-base", remote, "HEAD").strip(), f"{name} is behind origin/{name}, so the base comes from origin/{name}."
    if git_ok(root, "merge-base", "--is-ancestor", remote, local):
        return git(root, "merge-base", local, "HEAD").strip(), f"{name} is ahead of origin/{name}, so the base comes from {name}."
    from_local, from_remote = git(root, "merge-base", local, "HEAD").strip(), git(root, "merge-base", remote, "HEAD").strip()
    newer = from_remote if git_ok(root, "merge-base", "--is-ancestor", from_local, from_remote) else from_local
    return newer, (f"{name} and origin/{name} have diverged; the base is the later of their merge-bases with HEAD, "
                   f"{newer[:10]}.")


def current_branch(root: Path) -> str:
    result = subprocess.run(["git", "-C", str(root), "symbolic-ref", "--quiet", "--short", "HEAD"], capture_output=True, text=True)
    return result.stdout.strip()


def stack_parent(root: Path) -> str:
    """The branch a /atdd:cover-queue unit branch was cut from, recorded by the preparer as branch.<name>.atddBase."""
    branch = current_branch(root)
    if not branch:
        return ""
    result = subprocess.run(["git", "-C", str(root), "config", "--get", f"branch.{branch}.atddBase"], capture_output=True, text=True)
    return result.stdout.strip()


def resolve_base(root: Path, config: dict, override: str | None) -> tuple:
    """(base commit, note). ATDD_BASE wins. A /atdd:cover-queue unit branch is judged against its stack parent (then
    the integration branch, once the parent is merged and deleted), so the gate sees exactly that unit's change.
    Everything else uses the merge-base with baseBranch."""
    if override:
        return git(root, "rev-parse", override).strip(), ""
    branch = config.get("baseBranch", "main")
    candidates = [branch]
    parent = stack_parent(root)
    if parent:
        integration = str((config.get("cover") or {}).get("integrationBranch") or "atdd/cover-base")
        candidates = [parent, integration, branch]
    skipped = []
    for name in dict.fromkeys(candidates):
        base, note = merge_base_with(root, name)
        if base:
            if parent and name != parent:
                note = " ".join(filter(None, [f"The stack parent {parent} no longer exists; the base comes from {name}.", note]))
            return base, note
        skipped.append(name)
    raise SystemExit(f"Can't find the merge-base with any of: {', '.join(skipped)} (local or origin/). Set ATDD_BASE or "
                     "baseBranch in .claude/atdd.json.")


def changed_files(root: Path, base: str) -> list:
    tracked = git(root, "diff", "--name-only", "--diff-filter=d", base).splitlines()
    untracked = git(root, "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({p for p in tracked + untracked if p and (root / p).is_file()})


def added_lines(root: Path, base: str, files: list) -> dict:
    """Map file -> {line number: text} for lines added since base (whole file for untracked files)."""
    result: dict = {}
    diff = git(root, "diff", "-U0", "--no-color", "--diff-filter=d", base)
    current = None
    line_no = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            current = raw[6:] if raw.startswith("+++ b/") else None
            if current:
                result.setdefault(current, {})
            continue
        hunk = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", raw)
        if hunk:
            line_no = int(hunk.group(1))
            continue
        if current and raw.startswith("+"):
            result[current][line_no] = raw[1:]
            line_no += 1
    untracked = set(git(root, "ls-files", "--others", "--exclude-standard").splitlines())
    for path in files:
        if path in untracked:
            try:
                text = (root / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            result[path] = {i: line for i, line in enumerate(text.splitlines(), start=1)}
    return result


# ---------- checks ----------

def check_build(g: Gate, r: Result) -> None:
    override = g.command_override("build")
    if g.stack == "dotnet":
        solution = g.gate_config.get("solution", "")
        command = override or (f"dotnet build {shlex.quote(solution)} " if solution else "dotnet build ") + \
            "--no-incremental --tl:off '-clp:NoSummary;ForceNoAlign;DisableConsoleColor'"
        code = g.run("build", command)
        r.log = g.rel(g.log_path("build"))
        diagnostics = parse_msbuild(g.log_path("build").read_text(encoding="utf-8", errors="replace"), g.root)
        errors = [d for d in diagnostics if d["level"] == "error"]
        changed = set(g.code_files(include_tests=True))
        warnings = sorted({(d["file"], d["line"], d["code"], d["message"]) for d in diagnostics
                           if d["level"] == "warning" and d["file"] in changed and g.touched(d["file"], d["line"])})
        listing = "\n".join(f"{f}:{line}: {c} {m}" for f, line, c, m in warnings)
        (g.out / "build-warnings.txt").write_text(listing + ("\n" if listing else ""), encoding="utf-8")
        r.metric = f"{len(warnings)} warning(s) in changed code"
        r.threshold = "0"
        if code != 0 or errors:
            r.status = "fail"
            r.detail = f"Build failed with {len(errors)} error(s).\n" + "\n".join(
                f"{d['file']}:{d['line']}: {d['code']} {d['message']}" for d in errors[:20])
        elif warnings:
            r.status = "fail"
            r.detail = (f"{len(warnings)} analyzer/compiler warning(s) in changed code (cognitive complexity S3776, "
                        "cyclomatic CA1502, coupling CA1506, unused IDE0051/52/60, nullability, ...). Code you add or "
                        "change must be warning-free:\n" + "\n".join(listing.splitlines()[:30]))
        return
    if g.node:
        if not override and not (g.root / "tsconfig.json").exists():
            r.status, r.detail = "skipped", "No tsconfig.json; nothing to type-check."
            return
        command = "npx --no-install tsc --noEmit"
        if g.stack == "nextjs" and package_has(g, "prisma"):
            command = "npx --no-install prisma generate && " + command
        code = g.run("build", override or command)
    else:
        dirs = " ".join(shlex.quote(d) for d in g.gate_config.get("sourceDirs", [])) or "."
        code = g.run("build", override or f"python3 -m compileall -q {dirs}")
    r.log = g.rel(g.log_path("build"))
    if code != 0:
        r.status, r.detail = "fail", "Build/type check failed:\n" + g.tail(g.log_path("build"), 25)


MSBUILD_LINE = re.compile(
    r"^\s*(?P<file>[^\s(][^(]*?)\((?P<line>\d+)(?:,\d+)*\)\s*:\s*(?P<level>warning|error)\s+(?P<code>[A-Za-z]+\d+)\s*:\s*(?P<message>.*?)(?:\s+\[[^\]]+\])?\s*$"
)


def parse_msbuild(text: str, root: Path) -> list:
    found = []
    for line in text.splitlines():
        match = MSBUILD_LINE.match(line)
        if not match:
            continue
        path = Path(match.group("file").strip())
        if not path.is_absolute():
            path = root / path
        try:
            rel = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = str(path)
        found.append({"file": rel, "line": int(match.group("line")), "level": match.group("level"),
                      "code": match.group("code"), "message": match.group("message")})
    return found


def check_format(g: Gate, r: Result) -> None:
    files = g.code_files(include_tests=True)
    override = g.command_override("format")
    if not files and not override:
        r.status, r.detail = "skipped", "No changed source files."
        return
    quoted = " ".join(shlex.quote(f) for f in files)
    if override:
        command = override
    elif g.stack == "dotnet":
        command = f"dotnet format whitespace . --folder --verify-no-changes --include {quoted}"
    elif g.node:
        if not package_has(g, "prettier"):
            r.status, r.detail = "skipped", "Prettier isn't a dev dependency; add a formatter or set gate.commands.format."
            return
        command = f"npx --no-install prettier --check {quoted}"
    else:
        if not g.has_tool("ruff"):
            r.status, r.detail = "fail", "ruff isn't installed (pip install ruff), or set gate.commands.format."
            return
        command = f"ruff format --check {quoted}"
    code = g.run("format", command)
    r.log = g.rel(g.log_path("format"))
    if code != 0:
        r.status, r.detail = "fail", "Files need formatting (run the formatter on them):\n" + g.tail(g.log_path("format"), 20)


def package_has(g: Gate, name: str) -> bool:
    package = g.root / "package.json"
    if not package.exists():
        return False
    data = json.loads(package.read_text(encoding="utf-8"))
    return any(name in (data.get(k) or {}) for k in ("dependencies", "devDependencies"))


def check_lint(g: Gate, r: Result) -> None:
    override = g.command_override("lint")
    files = g.code_files(include_tests=True)
    if g.stack == "dotnet" and not override:
        r.detail = "Analyzers run as part of the build check."
        return
    if override:
        code = g.run("lint", override)
        r.log = g.rel(g.log_path("lint"))
        if code != 0:
            r.status, r.detail = "fail", "Lint errors:\n" + g.tail(g.log_path("lint"), 30)
        return
    if not files:
        r.status, r.detail = "skipped", "No changed source files."
        return
    quoted = " ".join(shlex.quote(f) for f in files)
    report = g.out / "lint.json"
    if g.node:
        if not any((g.root / n).exists() for n in ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", "eslint.config.ts")):
            r.status, r.detail = "skipped", "No eslint.config.* in the repo; the cognitive check still enforces complexity."
            return
        code = g.run("lint", f"npx --no-install eslint --no-warn-ignored -f json -o {shlex.quote(str(report))} {quoted}")
        problems = eslint_problems(g, report)
    else:
        if not g.has_tool("ruff"):
            r.status, r.detail = "fail", "ruff isn't installed (pip install ruff), or set gate.commands.lint."
            return
        code = g.run("lint", f"ruff check --output-format json --output-file {shlex.quote(str(report))} {quoted}")
        problems = ruff_problems(g, report)
    r.log = g.rel(g.log_path("lint"))
    if problems is None:
        r.status, r.detail = "fail", f"The linter produced no report (exit {code}):\n" + g.tail(g.log_path("lint"), 20)
        return
    mine = [p for p in problems if p["fatal"] or g.touched_line(p["file"], p["line"])]
    r.metric = f"{len(mine)} problem(s) on changed lines"
    if mine:
        r.status = "fail"
        r.detail = "Lint problems on lines this change adds or modifies:\n" + "\n".join(
            f"{p['file']}:{p['line']}: {p['rule']} {p['message']}" for p in mine[:30])


def eslint_problems(g: Gate, report: Path):
    """ESLint JSON -> [{file, line, rule, message, fatal}], or None when there's no report."""
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    problems = []
    for entry in data:
        path = g.rel(Path(entry.get("filePath", "")))
        for message in entry.get("messages", []):
            if message.get("severity", 0) < 1:
                continue
            problems.append({"file": path, "line": message.get("line") or 0, "rule": message.get("ruleId") or "parse-error",
                             "message": message.get("message", ""), "fatal": bool(message.get("fatal"))})
    return problems


def ruff_problems(g: Gate, report: Path):
    """ruff JSON -> [{file, line, rule, message, fatal}], or None when there's no report."""
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return [{"file": g.rel(Path(item.get("filename", ""))), "line": (item.get("location") or {}).get("row") or 0,
             "rule": item.get("code") or "syntax-error", "message": item.get("message", ""), "fatal": not item.get("code")}
            for item in data]


def check_tests(g: Gate, r: Result) -> None:
    with_coverage = g.mode == "full"
    r.log = g.rel(g.log_path("tests"))
    override = g.command_override("testsWithCoverage" if with_coverage else "tests")
    if override:
        code = g.run("tests", override, env={"ATDD_OUT": str(g.out)})
        if with_coverage:
            g.coverage_reports = coverage_files(g)
    elif g.stack == "dotnet":
        solution = g.gate_config.get("solution", "")
        target = f"{shlex.quote(solution)} " if solution else ""
        if with_coverage:
            results = g.out / "test-results"
            code = g.run("tests", f"dotnet test {target}--collect:\"XPlat Code Coverage;Format=json,cobertura\" "
                                  f"--results-directory {shlex.quote(str(results))}")
            g.coverage_reports = sorted(glob.glob(str(results / "**" / "coverage.cobertura.xml"), recursive=True))
            g.coverlet_reports = sorted(glob.glob(str(results / "**" / "coverage.json"), recursive=True))
            g.branches_measured = True
        else:
            code = g.run("tests", f"dotnet test {target}".strip())
    elif g.node:
        code = run_node_tests(g, with_coverage)
    else:
        code = run_python_tests(g, r, with_coverage)
    if code != 0:
        r.status = "fail"
        r.detail = "\n\n".join(filter(None, ["Tests failed:\n" + g.tail(g.log_path("tests"), 30), r.detail]))


PYTEST_TESTS_FAILED = 1


def run_python_tests(g: Gate, r: Result, with_coverage: bool) -> int:
    """pytest, with its failures classified. When every failure is flaky the check reports flaky and, in full mode,
    measures coverage again without those tests. Returns the exit code that decides whether the check fails."""
    junit = g.out / "junit.xml"
    code = run_pytest(g, with_coverage, f"--junitxml={shlex.quote(str(junit))} -o junit_family=xunit1")
    if code != PYTEST_TESTS_FAILED:
        return code
    try:
        verdicts = classify_failures(g, junit)
    except GateError as error:
        r.detail = f"Can't tell whether the failures are flaky: {error}"
        return code
    if not verdicts:
        return code
    if not all(verdict.flaky for verdict in verdicts.values()):
        r.detail = "Not flaky:\n" + describe_verdicts(verdicts, flaky=False)
        return code
    g.flaky_tests = sorted(verdicts)
    r.status = "flaky"
    r.detail = (f"Every failing test is flaky: nothing it runs on the test side changed since the base {g.base[:10]}, "
                "and it reaches no production file this branch changed. Advisory; it doesn't fail the gate:\n"
                + describe_verdicts(verdicts, flaky=True))
    if not with_coverage:
        return 0
    rerun = run_pytest(g, with_coverage, "", append=True, env=without_flaky_tests(g))
    if rerun != 0:
        r.detail = (f"The coverage run without the flaky tests failed too (exit {rerun}), so coverage can't be "
                    "measured.\n\n" + r.detail)
    return rerun


def run_pytest(g: Gate, with_coverage: bool, extra: str, append: bool = False, env: dict | None = None) -> int:
    base = g.command_override("pytest") or "python3 -m pytest --ignore=mutants"
    if not with_coverage:
        return g.run("tests", f"{base} {extra}".strip(), append=append, env=env)
    report = g.out / "coverage.xml"
    cov = " ".join(f"--cov={shlex.quote(d)}" for d in g.gate_config.get("sourceDirs", [])) or "--cov=."
    g.coverage_reports = [str(report)]
    g.branches_measured = True
    return g.run("tests", f"{base} {cov} --cov-branch --cov-report=xml:{shlex.quote(str(report))} {extra}".strip(),
                 append=append, env=env)


class Verdict(NamedTuple):
    flaky: bool
    why: str


def describe_verdicts(verdicts: dict, flaky: bool) -> str:
    return "\n".join(f"{test}: {verdict.why}" for test, verdict in sorted(verdicts.items()) if verdict.flaky == flaky)


def classify_failures(g: Gate, junit: Path) -> dict:
    """{node id: Verdict} for every failing test in pytest's JUnit report; empty when the report doesn't say which
    module a failure ran from, so the failure stays a plain one. A failure is flaky when nothing on its test side
    changed since the base (its module, the conftest.py files above it, the test modules they import or load as
    pytest_plugins, and the .feature files any of them bind) and none of their imports reaches a production file this
    branch changed."""
    try:
        cases = list(ElementTree.parse(junit).getroot().iter("testcase"))
    except (OSError, ElementTree.ParseError):
        return {}
    failing = [(case, test_module_of(g, case)) for case in cases
               if case.find("failure") is not None or case.find("error") is not None]
    if not all(path for _, path in failing):
        return {}
    graph = PythonImportGraph(g)
    changed_production = set(g.code_files())
    changed_features = sorted(f for f in g.changed if f.endswith(".feature"))
    verdicts = {}
    for case, path in failing:
        files = [path] + conftests_above(g, path)
        reached = graph.reachable_from(files)
        test_side = sorted(set(files) | (reached - graph.production_paths))
        edited = ([f for f in test_side if f in g.changed or g.is_new_file(f)]
                  + graph.bound_features(test_side, changed_features))
        reached_production = sorted(reached & graph.production_paths)
        hit = [f for f in reached_production if f in changed_production]
        if edited:
            verdict = Verdict(False, f"{', '.join(edited)} added or changed on this branch")
        elif hit:
            verdict = Verdict(False, f"imports reach changed production code: {', '.join(hit)}")
        else:
            reach = ", ".join(reached_production) or "no production code"
            verdict = Verdict(True, f"{path} unchanged since the base; its imports reach {reach}, none of it changed")
        verdicts[node_id(case, path)] = verdict
    return verdicts


def test_module_of(g: Gate, case) -> str | None:
    """The test module a JUnit test case ran from. pytest-bdd reports its own scenario.py as the file, so the classname
    (the module's dotted path from the rootdir, then any test classes) decides, and the file only when it can't."""
    parts = (case.get("classname") or "").split(".")
    for size in range(len(parts), 0, -1):
        candidate = "/".join(parts[:size]) + ".py"
        if (g.root / candidate).is_file():
            return candidate
    path = posixpath.normpath(case.get("file") or "..")
    return path if not path.startswith("..") and (g.root / path).is_file() else None


def node_id(case, path: str) -> str:
    name, classname = case.get("name"), case.get("classname") or ""
    module = path[:-3].replace("/", ".")
    owner = classname[len(module) + 1:] if classname.startswith(module + ".") else ""
    return "::".join([path, *filter(None, owner.split(".")), name])


def conftests_above(g: Gate, path: str) -> list:
    found, folder = [], posixpath.dirname(path)
    while True:
        candidate = posixpath.join(folder, "conftest.py") if folder else "conftest.py"
        if (g.root / candidate).is_file():
            found.append(candidate)
        if not folder:
            return found
        folder = posixpath.dirname(folder)


class PythonImportGraph:
    """The production modules of sourceDirs, the test-side modules, and what they import, for following a test's
    imports to the code it runs. A test-side module answers to every name pytest's rootdir-based imports may give it
    (tests.unit.helpers, unit.helpers, helpers), so an ambiguous name reaches every module it may mean."""

    def __init__(self, g: Gate):
        self.root = g.root
        production = python_module_paths(g, g.production_files())
        self.production_paths = set(production.values())
        self.modules = {name: [path] for name, path in production.items()}
        self.name_of = {path: name for name, path in production.items()}
        for path in python_test_files(g):
            name = python_dotted(path)
            self.name_of[path] = name
            parts = name.split(".")
            for size in range(1, len(parts) + 1):
                suffix = ".".join(parts[-size:])
                if suffix not in production:
                    self.modules.setdefault(suffix, []).append(path)
        self.features_base = pytest_ini_option(g, "bdd_features_base_dir")
        self._trees = {}
        self._imports = {}

    def reachable_from(self, files: list) -> set:
        """Files the given files import, directly, transitively or as pytest_plugins, with the packages holding them."""
        pending = [target for f in files for target in self.imports_of(f)]
        seen = set()
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            pending.extend(target for path in self.modules[name] for target in self.imports_of(path))
            parent = name.rpartition(".")[0]
            if parent in self.modules:
                pending.append(parent)
        return {path for name in seen for path in self.modules[name]}

    def imports_of(self, path: str) -> list:
        if path not in self._imports:
            module = self.name_of.get(path) or python_dotted(path)
            tree = self.tree(path)
            targets = [target for _, target in python_imports(tree, module, python_package(module, path), self.modules)]
            plugins = [longest_known_prefix(plugin, self.modules) for plugin in pytest_plugins_of(tree)]
            self._imports[path] = targets + [target for target in plugins if target]
        return self._imports[path]

    def bound_features(self, files: list, features: list) -> list:
        """The given .feature files that pytest-bdd's scenarios() and scenario() calls in these files bind: all of them
        when a call names its feature with anything but string literals."""
        bound = set()
        for path in files:
            for target in self.feature_targets(path):
                if target is None:
                    return list(features)
                bound.update(f for f in features if f == target or f.startswith(target + "/"))
        return sorted(bound)

    def feature_targets(self, path: str):
        """The feature paths a file binds, from the repo root; None for one named by anything but a string literal."""
        base = posixpath.dirname(path) if self.features_base is None else self.features_base
        for node in ast.walk(self.tree(path)):
            if not isinstance(node, ast.Call):
                continue
            called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if called not in ("scenarios", "scenario"):
                continue
            names = node.args if called == "scenarios" else node.args[:1]
            if not names or not all(isinstance(n, ast.Constant) and isinstance(n.value, str) for n in names):
                yield None
                continue
            for name in names:
                yield posixpath.normpath(posixpath.join(base, name.value))

    def tree(self, path: str):
        if path not in self._trees:
            self._trees[path] = parse_python(self.root, path, "the gate can't tell whether a failing test that runs it "
                                                              "is flaky")
        return self._trees[path]


def python_test_files(g: Gate) -> list:
    listed = git(g.root, "ls-files", "-co", "--exclude-standard", "--", "*.py").splitlines()
    return sorted(p for p in set(listed) if g.is_test(p) and (g.root / p).is_file())


def python_dotted(path: str) -> str:
    name = path[:-3].replace("/", ".")
    return name[: -len(".__init__")] if name.endswith(".__init__") else name


def python_package(module: str, path: str) -> str:
    return (module if path.endswith("__init__.py") else module.rpartition(".")[0]) or "(top level)"


def parse_python(root: Path, path: str, unchecked: str):
    """The module's syntax tree; a GateError naming the file and what goes unchecked when it can't be read or parsed."""
    try:
        return ast.parse((root / path).read_text(encoding="utf-8"), filename=path)
    except SyntaxError as error:
        raise GateError(f"{path}:{error.lineno}: Python {sys.version.split()[0]} can't parse it ({error.msg}), so "
                        f"{unchecked}. Run the gate with the project's Python.") from None
    except (OSError, UnicodeDecodeError) as error:
        raise GateError(f"Can't read {path} ({error}), so {unchecked}.") from None


def pytest_plugins_of(tree) -> list:
    """The modules a top-level pytest_plugins assignment loads."""
    plugins = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in node.targets):
            values = node.value.elts if isinstance(node.value, (ast.List, ast.Tuple)) else [node.value]
            plugins += [v.value for v in values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
    return plugins


def pytest_ini_option(g: Gate, key: str) -> str | None:
    """An ini option, relative to the repo root, from the first pytest config file that sets it, in pytest's order."""
    for name in ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"):
        path = g.root / name
        if path.is_file():
            match = re.search(rf"^\s*{re.escape(key)}\s*=\s*(.+?)\s*$", path.read_text(encoding="utf-8"), re.MULTILINE)
            if match:
                return match.group(1).strip("'\"").strip("/") or "."
    return None


def coverage_files(g: Gate) -> list:
    pattern = g.gate_config.get("coverageReports")
    if not pattern:
        return []
    patterns = pattern if isinstance(pattern, list) else [pattern]
    return sorted({p for pat in patterns for p in glob.glob(str(g.root / pat), recursive=True)})


def check_undefined_steps(g: Gate, r: Result) -> None:
    override = g.command_override("undefinedSteps")
    if override:
        code = g.run("undefined-steps", override)
        r.log = g.rel(g.log_path("undefined-steps"))
        if code != 0:
            r.status, r.detail = "fail", "Unbound or ambiguous steps:\n" + g.tail(g.log_path("undefined-steps"), 30)
        return
    if g.stack == "dotnet":
        configs = [p for p in g.root.rglob("reqnroll.json") if "bin" not in p.parts and "obj" not in p.parts]
        strict = []
        for path in configs:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if str((data.get("runtime") or {}).get("missingOrPendingStepsOutcome", "")).lower() == "error":
                strict.append(g.rel(path))
        if not strict:
            r.status = "fail"
            r.detail = ('No reqnroll.json sets runtime.missingOrPendingStepsOutcome to "Error", so undefined or pending '
                        "steps wouldn't fail the tests. A human must fix the (locked) Reqnroll config.")
        else:
            r.detail = f"Strict Reqnroll config ({', '.join(strict)}); undefined or pending steps fail the tests check."
        return
    if g.node:
        runner = g.gate_config.get("acceptanceRunner", "playwright-bdd" if g.stack == "nextjs" else "cucumber")
        if runner == "playwright-bdd":
            code = g.run("undefined-steps", "npx --no-install bddgen")
            r.log = g.rel(g.log_path("undefined-steps"))
            if code != 0:
                r.status, r.detail = "fail", "bddgen found missing steps:\n" + g.tail(g.log_path("undefined-steps"), 30)
            return
        messages = g.out / "cucumber-dry-run.ndjson"
        code = g.run("undefined-steps", f"npx --no-install cucumber-js --dry-run --format message:{shlex.quote(str(messages))}")
        r.log = g.rel(g.log_path("undefined-steps"))
        text = messages.read_text(encoding="utf-8", errors="replace") if messages.exists() else ""
        undefined = len(re.findall(r'"status"\s*:\s*"UNDEFINED"', text))
        ambiguous = len(re.findall(r'"status"\s*:\s*"AMBIGUOUS"', text))
        r.metric = f"{undefined} undefined, {ambiguous} ambiguous"
        if code != 0 or not text:
            r.status, r.detail = "fail", "cucumber-js dry run failed:\n" + g.tail(g.log_path("undefined-steps"), 30)
        elif undefined or ambiguous:
            r.status, r.detail = "fail", f"{undefined} undefined and {ambiguous} ambiguous step(s); every step needs exactly one binding."
        return
    acceptance = g.root / g.gate_config.get("acceptanceTestsDir", "tests/acceptance")
    binders = [p for p in acceptance.rglob("*.py") if re.search(r"\bscenarios\(", p.read_text(encoding="utf-8", errors="replace"))] \
        if acceptance.is_dir() else []
    if not binders:
        r.status = "fail"
        r.detail = (f"No scenarios(...) call under {g.rel(acceptance)}, so scenarios can exist without being bound. "
                    "Bind the feature directory with pytest_bdd.scenarios() (see the gherkin-binding skill).")
    else:
        r.detail = (f"Scenarios are bound with scenarios() in {', '.join(g.rel(p) for p in binders)}; a missing step "
                    "fails the tests check with StepDefinitionNotFoundError.")


@dataclass
class StepDefinition:
    location: str
    keywords: tuple       # the step keywords it binds; node runners ignore keywords
    kind: str             # exact | parse | cfparse | re | regex
    text: str

    def key(self, keyword_agnostic: bool) -> tuple:
        return (("*",) if keyword_agnostic else self.keywords, self.kind, self.text)


PYTHON_STEP_DECORATORS = {"given": ("given",), "when": ("when",), "then": ("then",), "step": ("given", "when", "then")}
NODE_STEP = re.compile(r"""\b(Given|When|Then|Step|defineStep)\s*\(\s*(?:(['"`])((?:\\.|(?!\2)[^\\])*)\2|/((?:\\.|[^/\\\n])+)/[a-z]*)""")
DOTNET_STEP = re.compile(r'\[\s*(?:Reqnroll\.|TechTalk\.SpecFlow\.)?(Given|When|Then|StepDefinition)(?:Attribute)?\s*\(\s*(@?)"((?:""|\\.|[^"\\])*)"')
FEATURE_STEP = re.compile(r"^\s*(Given|When|Then|And|But|\*)\s+(.+?)\s*$")


def step_definitions(g: Gate) -> list:
    """Every step definition in the test paths, changed or not: a duplicate is between a new and an existing one."""
    listed = git(g.root, "ls-files", "-co", "--exclude-standard").splitlines()
    files = sorted(p for p in set(listed) if p.endswith(g.profile["extensions"]) and g.in_test_paths(p)
                   and not g.is_generated(p) and (g.root / p).is_file())
    found = []
    for path in files:
        text = (g.root / path).read_text(encoding="utf-8", errors="replace")
        if g.stack == "python":
            found += python_step_definitions(path, text)
        elif g.node:
            for match in NODE_STEP.finditer(text):
                literal, regex = match.group(3), match.group(4)
                if literal is not None and "${" in literal:
                    continue
                line = text.count("\n", 0, match.start()) + 1
                found.append(StepDefinition(f"{path}:{line}", ("*",), "regex" if regex else "exact",
                                            regex if regex else literal))
        else:
            for match in DOTNET_STEP.finditer(text):
                word = match.group(1).lower()
                keywords = ("given", "when", "then") if word == "stepdefinition" else (word,)
                raw = match.group(3).replace('""', '"') if match.group(2) else match.group(3)
                line = text.count("\n", 0, match.start()) + 1
                found += [StepDefinition(f"{path}:{line}", (k,), "regex", raw) for k in keywords]
    return found


def python_step_definitions(path: str, text: str) -> list:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            func = decorator.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in PYTHON_STEP_DECORATORS:
                continue
            argument = decorator.args[0]
            kind, pattern = "exact", None
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                pattern = argument.value
            elif isinstance(argument, ast.Call) and argument.args and isinstance(argument.args[0], ast.Constant):
                parser = argument.func.attr if isinstance(argument.func, ast.Attribute) else getattr(argument.func, "id", "")
                kind = parser if parser in ("parse", "cfparse", "re") else "parse"
                pattern = argument.args[0].value if isinstance(argument.args[0].value, str) else None
            if pattern is not None:
                found += [StepDefinition(f"{path}:{decorator.lineno}", (k,), kind, pattern)
                          for k in PYTHON_STEP_DECORATORS[name]]
    return found


def parse_format_regex(pattern: str) -> re.Pattern:
    """A regex for a parse/cfparse format: literal text, with each {field} matching anything."""
    pieces, i = [], 0
    while i < len(pattern):
        if pattern.startswith("{{", i) or pattern.startswith("}}", i):
            pieces.append(re.escape(pattern[i]))
            i += 2
        elif pattern[i] == "{":
            end = pattern.find("}", i)
            if end < 0:
                pieces.append(re.escape(pattern[i:]))
                break
            pieces.append("(.+?)")
            i = end + 1
        else:
            pieces.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(pieces), re.DOTALL)


def python_step_matches(definition: StepDefinition, text: str) -> bool:
    if definition.kind == "exact":
        return definition.text == text
    try:
        regex = re.compile(definition.text) if definition.kind == "re" else parse_format_regex(definition.text)
    except re.error:
        return False
    return regex.fullmatch(text) is not None


def feature_steps(g: Gate) -> list:
    """(keyword, text, location) for every step in the working tree's .feature files, outlines expanded."""
    features = str(g.config.get("featuresDir", "features")).strip("/") or "."
    steps = []
    for path in sorted((g.root / features).rglob("*.feature")) if (g.root / features).is_dir() else []:
        rel = g.rel(path)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        current, outline, examples = "given", [], None
        pending = []

        def flush():
            for keyword, text, location in pending:
                rows = examples or [{}]
                for row in rows:
                    expanded = re.sub(r"<([^<>]+)>", lambda m: row.get(m.group(1), m.group(0)), text)
                    steps.append((keyword, expanded, location))
            pending.clear()

        header = None
        for number, raw in enumerate(lines, start=1):
            stripped = raw.strip()
            if re.match(r"^(Scenario|Scenario Outline|Scenario Template|Example|Background|Rule|Feature):", stripped):
                flush()
                examples, header, current = None, None, "given"
                continue
            if re.match(r"^(Examples|Scenarios):", stripped):
                examples, header = [], None
                continue
            if examples is not None and stripped.startswith("|"):
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                if header is None:
                    header = cells
                else:
                    examples.append(dict(zip(header, cells)))
                continue
            match = FEATURE_STEP.match(raw)
            if match and examples is None:
                word = match.group(1).lower()
                current = current if word in ("and", "but", "*") else word
                pending.append((current, match.group(2), f"{rel}:{number}"))
        flush()
    return steps


def check_duplicate_steps(g: Gate, r: Result) -> None:
    r.threshold = "each step text has exactly one definition"
    definitions = step_definitions(g)
    if not definitions:
        r.status, r.detail = "skipped", "No step definitions in the test paths."
        return
    agnostic = g.node  # Cucumber and playwright-bdd match a definition whatever keyword the step uses
    groups = {}
    for definition in definitions:
        groups.setdefault(definition.key(agnostic), []).append(definition.location)
    duplicates = [f"{text!r} ({kind}{'' if agnostic else ', ' + '/'.join(keywords)}) is defined {len(locations)} times: "
                  + ", ".join(sorted(set(locations)))
                  for (keywords, kind, text), locations in sorted(groups.items()) if len(set(locations)) > 1]
    ambiguous = []
    if g.stack == "python":
        # pytest-bdd doesn't fail when two definitions match a step: the one registered last wins, silently.
        seen = set()
        for keyword, text, location in feature_steps(g):
            matching = sorted({d.location for d in definitions if keyword in d.keywords and python_step_matches(d, text)})
            if len(matching) > 1 and (keyword, text) not in seen:
                seen.add((keyword, text))
                ambiguous.append(f"{location} {keyword.capitalize()} {text!r} matches {len(matching)} definitions: "
                                 + ", ".join(matching))
    log = g.log_path("duplicate-steps")
    log.write_text("".join(f"{d.location}\t{'/'.join(d.keywords)}\t{d.kind}\t{d.text}\n" for d in definitions)
                   + "".join(f"DUPLICATE {line}\n" for line in duplicates)
                   + "".join(f"AMBIGUOUS {line}\n" for line in ambiguous), encoding="utf-8")
    r.log = g.rel(log)
    r.metric = f"{len(definitions)} step definition(s), {len(duplicates)} duplicated, {len(ambiguous)} ambiguous step(s)"
    if duplicates or ambiguous:
        r.status = "fail"
        r.detail = ("Every step needs exactly one definition. Find the existing one and reuse it, extending it if it has "
                    "to; steps more than one feature uses belong in one shared place (such as the steps' common/ "
                    "folder or the acceptance tests' conftest.py), and the copy goes:\n"
                    + "\n".join((duplicates + ambiguous)[:30]))


def check_complexity(g: Gate, r: Result) -> None:
    files = g.subject_files()
    t = g.thresholds
    r.threshold = f"CCN ≤ {t['cyclomatic']}, NLOC ≤ {t['functionLength']}, params ≤ {t['parameters']}"
    if not files:
        r.status, r.detail = "skipped", f"No changed {g.subject}."
        return
    if not g.has_tool("lizard"):
        r.status, r.detail = "fail", "lizard isn't installed (pip install lizard==1.24.3)."
        return
    functions = g.functions()
    over = []
    for path in files:
        for fn in functions.get(path, []):
            if not g.touched(path, fn["start"], fn["end"]):
                continue
            problems = []
            if fn["ccn"] > t["cyclomatic"]:
                problems.append(f"cyclomatic complexity {fn['ccn']} > {t['cyclomatic']}")
            if fn["nloc"] > t["functionLength"]:
                problems.append(f"{fn['nloc']} lines of code > {t['functionLength']}")
            if fn["params"] > t["parameters"]:
                problems.append(f"{fn['params']} parameters > {t['parameters']}")
            if problems:
                over.append(f"{path}:{fn['start']}-{fn['end']} {fn['name']}: " + ", ".join(problems))
    log = g.log_path("complexity")
    log.write_text("\n".join(over) + ("\n" if over else ""), encoding="utf-8")
    r.log = g.rel(log)
    r.metric = f"{len(over)} changed function(s) over a limit"
    if over:
        r.status = "fail"
        r.detail = "Changed functions over the cyclomatic complexity, length or parameter limits:\n" + "\n".join(over[:30])


def check_cognitive(g: Gate, r: Result) -> None:
    files = g.subject_files()
    limit = g.thresholds["cognitive"]
    r.threshold = f"≤ {limit} per function"
    override = g.command_override("cognitive")
    if override:
        code = g.run("cognitive", override)
        r.log = g.rel(g.log_path("cognitive"))
        if code != 0:
            r.status, r.detail = "fail", f"Functions over cognitive complexity {limit}:\n" + g.tail(g.log_path("cognitive"), 30)
        return
    if g.stack == "dotnet":
        editorconfig = g.root / ".editorconfig"
        text = editorconfig.read_text(encoding="utf-8", errors="replace") if editorconfig.exists() else ""
        if not re.search(r"dotnet_diagnostic\.S3776\.severity\s*=\s*(warning|error)", text):
            r.status = "fail"
            r.detail = ("S3776 (SonarAnalyzer cognitive complexity) isn't enabled in .editorconfig, so cognitive complexity "
                        "isn't measured. A human must apply the atdd .NET analyzer setup (/atdd:init).")
        else:
            r.detail = "Measured by SonarAnalyzer S3776 during the build check (threshold in SonarLint.xml)."
        return
    if not files:
        r.status, r.detail = "skipped", f"No changed {g.subject}."
        return
    quoted = " ".join(shlex.quote(f) for f in files)
    report = g.out / "cognitive.json"
    over = []
    if g.node:
        config = g.root / "scripts" / "atdd" / "eslint.quality.mjs"
        if not config.exists():
            r.status, r.detail = "fail", "scripts/atdd/eslint.quality.mjs is missing; run /atdd:init."
            return
        code = g.run("cognitive", f"npx --no-install eslint -c {shlex.quote(str(config))} --no-warn-ignored -f json "
                                  f"-o {shlex.quote(str(report))} {quoted}", env={"ATDD_COGNITIVE": str(limit)})
        problems = eslint_problems(g, report)
        if problems is None:
            r.status, r.detail = "fail", f"ESLint produced no report (exit {code}):\n" + g.tail(g.log_path("cognitive"), 20)
            return
        over = [f"{p['file']}:{p['line']}: {p['message']}" for p in problems
                if p["fatal"] or (p["rule"] == "sonarjs/cognitive-complexity" and g.touched(p["file"], p["line"]))]
    else:
        if not g.has_tool("complexipy"):
            r.status, r.detail = "fail", "complexipy isn't installed (pip install complexipy)."
            return
        code = g.run("cognitive", f"complexipy {quoted} --max-complexity-allowed {limit} --output-format json "
                                  f"--output {shlex.quote(str(report))}")
        try:
            entries = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            r.status, r.detail = "fail", f"complexipy produced no report (exit {code}):\n" + g.tail(g.log_path("cognitive"), 20)
            return
        functions = g.functions()
        for entry in entries:
            if entry.get("complexity", 0) <= limit:
                continue
            path = entry.get("path", "")
            name = str(entry.get("function_name", "")).replace("::", ".").split(".")[-1]
            ranges = [fn for fn in functions.get(path, []) if fn["name"].replace("::", ".").split(".")[-1] == name]
            if not ranges or any(g.touched(path, fn["start"], fn["end"]) for fn in ranges):
                over.append(f"{path}: {entry.get('function_name')} has cognitive complexity {entry.get('complexity')} > {limit}")
    r.log = g.rel(g.log_path("cognitive"))
    r.metric = f"{len(over)} changed function(s) over {limit}"
    if over:
        r.status = "fail"
        r.detail = f"Changed functions over cognitive complexity {limit} (flatten nesting, extract named steps):\n" + "\n".join(over[:30])


def check_diff_coverage(g: Gate, r: Result) -> None:
    files = g.code_files()
    limit = g.thresholds["diffCoverage"]
    r.threshold = f"≥ {limit}% of changed lines"
    if not files:
        r.status, r.detail = "skipped", "No changed production code."
        return
    reports = [p for p in g.coverage_reports if Path(p).exists()]
    if not reports:
        r.status, r.detail = "fail", "The tests check produced no coverage report, so diff coverage can't be measured."
        return
    if not g.has_tool("diff-cover"):
        r.status, r.detail = "fail", "diff-cover isn't installed (pip install diff-cover)."
        return
    out = g.out / "diff-cover.json"
    base_args = ["diff-cover", *reports, f"--compare-branch={g.base}", f"--fail-under={limit}"]
    code = g.run("diff-coverage", base_args + [f"--format=json:{out}"])
    if code == 2 or not out.exists():
        code = g.run("diff-coverage", base_args + ["--json-report", str(out)], append=True)
    r.log = g.rel(g.log_path("diff-coverage"))
    if not out.exists():
        r.status, r.detail = "fail", "diff-cover produced no report:\n" + g.tail(g.log_path("diff-coverage"), 20)
        return
    data = json.loads(out.read_text(encoding="utf-8"))
    lines = data.get("total_num_lines", 0)
    percent = data.get("total_percent_covered", 0)
    r.metric = f"{percent}% of {lines} changed line(s)"
    uncovered = []
    for path, stats in sorted((data.get("src_stats") or {}).items()):
        missing = stats.get("violation_lines") or []
        if missing:
            uncovered.append(f"{path}: lines {', '.join(str(n) for n in missing)}")
    (g.out / "uncovered-lines.txt").write_text("\n".join(uncovered) + ("\n" if uncovered else ""), encoding="utf-8")
    measured = set((data.get("src_stats") or {}).keys())
    unmeasured = [f for f in files if g.added_lines.get(f) and f not in measured]
    if unmeasured:
        r.status = "fail"
        r.detail = ("Changed production files are missing from the coverage reports, so their lines aren't measured "
                    "(not loaded by any test, or excluded from coverage):\n" + "\n".join(unmeasured))
        return
    if lines == 0:
        r.status = "fail"
        r.detail = ("Production code changed, but the coverage reports contain none of the changed lines. Usually the "
                    "report paths don't match the repo paths; a silent 100% would be wrong.")
        return
    branch_limit = g.thresholds["diffBranchCoverage"]
    branches = changed_branches(g, reports, set(files))
    r.threshold = f"≥ {limit}% of changed lines, ≥ {branch_limit}% of their branches"
    r.metric += f"; {branches.describe()}"
    (g.out / "uncovered-branches.txt").write_text("\n".join(branches.gaps) + ("\n" if branches.gaps else ""), encoding="utf-8")
    problems = []
    if code != 0 or percent < limit:
        problems.append(f"Line coverage {percent}% is below {limit}%. Uncovered changed lines:\n" + "\n".join(uncovered[:30]))
    if branches.error:
        problems.append(branches.error)
    elif branches.percent() < branch_limit:
        problems.append(f"Branch coverage {branches.percent()}% is below {branch_limit}%. Changed lines with branches "
                        "no test takes:\n" + "\n".join(branches.gaps[:30]))
    if problems:
        r.status, r.detail = "fail", "\n\n".join(problems)


@dataclass
class BranchCoverage:
    covered: int = 0
    total: int = 0
    gaps: list = field(default_factory=list)
    error: str = ""

    def percent(self) -> float:
        return round(100.0 * self.covered / self.total, 1) if self.total else 100.0

    def describe(self) -> str:
        if self.error:
            return "branches not measured"
        return f"{self.percent()}% of {self.total} branch(es) on them" if self.total else "no branches on them"


def changed_branches(g: Gate, reports: list, files: set) -> BranchCoverage:
    """Branch coverage of the changed lines. Coverlet's JSON names every branch, so branches taken by different test
    projects add up; Cobertura only counts them per line, so there the best report for a line counts."""
    exact = coverlet_branches(g, g.coverlet_reports, files) if g.coverlet_reports else None
    lines, has_branch_data = exact or cobertura_branches(g, reports, files)
    result = BranchCoverage()
    if not has_branch_data and not g.branches_measured:
        result.error = ("The coverage reports carry no branch data at all, so branch coverage can't be measured. Turn on "
                        "branch coverage in the test run that writes them (see the quality-gate skill).")
        return result
    for (path, number), (covered, total) in sorted(lines.items()):
        result.covered += covered
        result.total += total
        if covered < total:
            result.gaps.append(f"{path}:{number}: {covered} of {total} branches taken")
    return result


def coverlet_branches(g: Gate, reports: list, files: set):
    """{(path, line): (covered, total)} from coverlet JSON: {module: {file: {class: {method: {"Lines": {...},
    "Branches": [{Line, Offset, EndOffset, Path, Ordinal, Hits}]}}}}}. None when a report can't be read."""
    hits = {}
    has_branch_data = False
    for report in reports:
        try:
            modules = json.loads(Path(report).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return None
        for documents in modules.values():
            for document, classes in documents.items():
                path = cobertura_path(g, document, [], files)
                for methods in classes.values():
                    for method, data in methods.items():
                        for branch in (data or {}).get("Branches") or []:
                            if branch.get("IsReachable") is False:
                                continue
                            has_branch_data = True
                            line = branch.get("Line", 0)
                            if path in files and line in g.added_lines.get(path, {}):
                                key = (path, line, method, branch.get("Offset"), branch.get("EndOffset"),
                                       branch.get("Path"), branch.get("Ordinal"))
                                hits[key] = hits.get(key, 0) + branch.get("Hits", 0)
    lines = {}
    for (path, line, *_), count in hits.items():
        covered, total = lines.get((path, line), (0, 0))
        lines[(path, line)] = (covered + (count > 0), total + 1)
    return lines, has_branch_data


def cobertura_branches(g: Gate, reports: list, files: set):
    """{(path, line): (covered, total)} from the condition-coverage of each Cobertura line."""
    best = {}
    has_branch_data = False
    for report in reports:
        try:
            root = ElementTree.parse(report).getroot()
        except (OSError, ElementTree.ParseError):
            continue
        sources = [s.text.strip() for s in root.iter("source") if s.text and s.text.strip()]
        for cls in root.iter("class"):
            path = cobertura_path(g, cls.get("filename", ""), sources, files)
            for line in cls.findall("./lines/line"):
                match = re.search(r"\((\d+)/(\d+)\)", line.get("condition-coverage", ""))
                if line.get("branch", "").lower() != "true" or not match or int(match.group(2)) == 0:
                    continue
                has_branch_data = True
                number = int(line.get("number", "0"))
                if path not in files or number not in g.added_lines.get(path, {}):
                    continue
                covered, total = int(match.group(1)), int(match.group(2))
                previous = best.get((path, number))
                if previous is None or covered * previous[1] > previous[0] * total:
                    best[(path, number)] = (covered, total)
    return best, has_branch_data


def cobertura_path(g: Gate, filename: str, sources: list, known: set) -> str:
    name = filename.replace("\\", "/")
    candidates = [Path(name)] if Path(name).is_absolute() else [Path(source) / name for source in sources] + [g.root / name]
    for candidate in candidates:
        try:
            relative = candidate.resolve().relative_to(g.root.resolve()).as_posix()
        except (ValueError, OSError):
            continue
        if relative in known:
            return relative
    return normalize_report_path(name, dict.fromkeys(known))


def mutation_score(report: Path, changed_lines: dict) -> dict:
    data = json.loads(report.read_text(encoding="utf-8"))
    detected, undetected, survivors = 0, 0, []
    for path, entry in (data.get("files") or {}).items():
        normalized = normalize_report_path(path, changed_lines)
        lines = changed_lines.get(normalized)
        for mutant in entry.get("mutants", []):
            status = mutant.get("status")
            line = ((mutant.get("location") or {}).get("start") or {}).get("line")
            if lines is not None and line not in lines:
                continue
            if lines is None and changed_lines:
                continue
            if status in ("Killed", "Timeout"):
                detected += 1
            elif status in ("Survived", "NoCoverage"):
                undetected += 1
                survivors.append(f"{normalized}:{line}: {mutant.get('mutatorName')} -> {str(mutant.get('replacement', '')).strip()[:80]} [{status}]")
    total = detected + undetected
    return {"detected": detected, "undetected": undetected, "total": total,
            "score": round(100.0 * detected / total, 1) if total else None, "survivors": survivors}


def normalize_report_path(path: str, changed_lines: dict) -> str:
    candidate = path.replace("\\", "/")
    if candidate in changed_lines:
        return candidate
    for known in changed_lines:
        if candidate.endswith("/" + known) or known.endswith("/" + candidate):
            return known
    return candidate


def check_mutation(g: Gate, r: Result) -> None:
    files = g.code_files()
    limit = g.thresholds["mutationScore"]
    r.threshold = f"≥ {limit}% of mutants on changed lines killed"
    if not files:
        r.status, r.detail = "skipped", "No changed production code to mutate."
        return
    changed_lines = {f: set(g.added_lines.get(f, {}).keys()) for f in files}
    override = g.command_override("mutation")
    report = None
    if g.stack == "dotnet":
        out = g.out / "stryker"
        solution = g.gate_config.get("solution", "")
        if not solution and not override:
            r.status, r.detail = "fail", "Set gate.solution in .claude/atdd.json so Stryker.NET knows what to mutate."
            return
        high, low = max(limit, 90), limit
        shutil.rmtree(out, ignore_errors=True)
        command = override or (
            f"dotnet stryker --solution {shlex.quote(solution)} --since:{g.base} -r json -r cleartext -r progress "
            f"--threshold-high {high} --threshold-low {low} --break-at {limit} -O {shlex.quote(str(out))}")
        code = g.run("mutation", command)
        report = out / "reports" / "mutation-report.json"
    elif g.node:
        incremental = g.root / ".atdd" / "cache" / "stryker-incremental.json"
        incremental.parent.mkdir(parents=True, exist_ok=True)
        report = g.root / g.gate_config.get("mutationReport", "reports/mutation/mutation.json")
        report.unlink(missing_ok=True)
        command = override or (
            f"npx --no-install stryker run --mutate {shlex.quote(','.join(glob_literal(f) for f in files))} "
            f"--reporters json,clear-text,progress --incremental --incrementalFile {shlex.quote(str(incremental))}")
        code = g.run("mutation", command)
    else:
        if not g.has_tool("mutmut"):
            r.status, r.detail = "fail", "mutmut isn't installed (pip install mutmut)."
            return
        modules = sorted({python_module(f) for f in files})
        command = override or "mutmut run " + " ".join(shlex.quote(f"{m}.*") for m in modules)
        env = without_flaky_tests(g)
        code = g.run("mutation", command, env=env)
        r.log = g.rel(g.log_path("mutation"))
        g.run("mutation", "mutmut export-cicd-stats", append=True, env=env)
        g.run("mutation-results", "mutmut results", env=env)
        stats_path = g.root / "mutants" / "mutmut-cicd-stats.json"
        if code != 0 or not stats_path.exists():
            r.status, r.detail = "fail", f"mutmut failed (exit {code}); the clean test run must pass first:\n" + g.tail(g.log_path("mutation"), 25)
            return
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        total = stats.get("total", 0) - stats.get("skipped", 0)
        if total <= 0:
            r.status, r.detail = "skipped", "mutmut generated no mutants for the changed modules."
            return
        score = round(100.0 * (stats.get("killed", 0) + stats.get("timeout", 0)) / total, 1)
        r.metric = f"{score}% killed ({stats.get('survived', 0)} survived, {stats.get('no_tests', 0)} without tests) in {', '.join(modules)}"
        survivors = [line.strip() for line in g.log_path("mutation-results").read_text(encoding="utf-8", errors="replace").splitlines()
                     if re.search(r":\s*(survived|no tests|timeout|suspicious)\s*$", line)]
        (g.out / "mutation-survivors.txt").write_text("\n".join(survivors) + ("\n" if survivors else ""), encoding="utf-8")
        if score < limit:
            r.status = "fail"
            r.detail = (f"Mutation score {score}% is below {limit}% for the changed modules. Surviving mutants "
                        f"(inspect with `mutmut show <name>`):\n" + "\n".join(survivors[:30]))
        return

    r.log = g.rel(g.log_path("mutation"))
    if not report or not report.exists():
        r.status, r.detail = "fail", f"Mutation testing didn't produce a report (exit {code}):\n" + g.tail(g.log_path("mutation"), 25)
        return
    result = mutation_score(report, changed_lines)
    (g.out / "mutation-survivors.txt").write_text("\n".join(result["survivors"]) + ("\n" if result["survivors"] else ""), encoding="utf-8")
    if result["score"] is None:
        r.status, r.detail = "skipped", "No valid mutants on the changed lines (only declarations or untestable code changed)."
        return
    r.metric = f"{result['score']}% ({result['detected']}/{result['total']} mutants on changed lines killed)"
    if result["score"] < limit:
        r.status = "fail"
        r.detail = (f"Mutation score {result['score']}% is below {limit}%. Each survivor is a behavior no test pins down:\n"
                    + "\n".join(result["survivors"][:40]))


def without_flaky_tests(g: Gate) -> dict:
    """Environment that makes every pytest run, mutmut's included, deselect the tests classified flaky."""
    if not g.flaky_tests:
        return {}
    return appended_to_environment("PYTEST_ADDOPTS", " ".join(f"--deselect {shlex.quote(t)}" for t in g.flaky_tests))


def appended_to_environment(name: str, value: str, separator: str = " ") -> dict:
    """{name: value} added after what the environment already holds for name."""
    return {name: separator.join(p for p in (os.environ.get(name, ""), value) if p)}


def glob_literal(path: str) -> str:
    """A glob that matches exactly this path, e.g. for Next.js route folders like [id] and (group)."""
    return re.sub(r"([][(){}*?!+@])", r"\\\1", path)


def python_module(path: str) -> str:
    module = path[:-3] if path.endswith(".py") else path
    module = module.replace("/", ".")
    if module.endswith(".__init__"):
        module = module[: -len(".__init__")]
    return module[4:] if module.startswith("src.") else module


def check_duplication(g: Gate, r: Result) -> None:
    files = g.code_files(include_tests=True)
    if not files:
        r.status, r.detail = "skipped", "No changed source files."
        return
    if not g.has_tool("npx"):
        r.status, r.detail = "fail", "jscpd needs Node.js (npx) on the PATH."
        return
    patterns = list(g.profile["generated_patterns"]) + list(g.gate_config.get("exclude", [])) + ["node_modules/*"]
    ignore = ",".join(sorted({to_jscpd_glob(p) for p in patterns}))
    out = g.out / "jscpd"
    command = g.command_override("duplication") or [
        "npx", "--yes", JSCPD, ".", "--format", g.profile["jscpd_format"], "--min-tokens", "50",
        "--min-lines", "5", "--ignore", ignore, "--reporters", "json,console", "--output", str(out),
        "--baseline-from-ref", g.base, "--fail-on-new-clones", "0", "--no-tips", "--no-colors"]
    shutil.rmtree(out, ignore_errors=True)
    code = g.run("duplication", command)
    r.log = g.rel(g.log_path("duplication"))
    r.threshold = "0 new clones"
    report = out / "jscpd-report.json"
    clones = []
    if report.exists():
        data = json.loads(report.read_text(encoding="utf-8"))
        r.metric = f"{((data.get('statistics') or {}).get('total') or {}).get('newClones', '?')} new clone(s)"
        for dup in data.get("duplicates", []):
            if dup.get("isNew", True):
                first, second = dup.get("firstFile", {}), dup.get("secondFile", {})
                clones.append(f"{first.get('name')}:{first.get('start')}-{first.get('end')} duplicates "
                              f"{second.get('name')}:{second.get('start')}-{second.get('end')} "
                              f"({dup.get('lines')} lines, {dup.get('tokens')} tokens)")
    if code != 0:
        r.status = "fail"
        r.detail = ("New duplicated code compared with the base commit. Extract what shares a reason to change; "
                    "keep apart what only looks alike:\n" + ("\n".join(clones[:20]) or g.tail(g.log_path("duplication"), 20)))


def to_jscpd_glob(pattern: str) -> str:
    """Turn a gate pattern (fnmatch, where * spans directories) into a jscpd/minimatch glob."""
    if pattern.startswith("*/"):
        pattern = "**/" + pattern[2:]
    elif "/" not in pattern or not pattern.startswith("**/"):
        pattern = "**/" + pattern
    if pattern.endswith("/*"):
        pattern = pattern[:-2] + "/**"
    return pattern


def check_architecture(g: Gate, r: Result) -> None:
    override = g.command_override("architecture")
    if override:
        code = g.run("architecture", override)
        r.log = g.rel(g.log_path("architecture"))
        if code != 0:
            r.status, r.detail = "fail", "Architecture rules broken:\n" + g.tail(g.log_path("architecture"), 30)
        return
    if g.stack == "dotnet":
        users = [p for p in g.root.rglob("*.cs") if "bin" not in p.parts and "obj" not in p.parts
                 and re.search(r"using\s+(ArchUnitNET|NetArchTest)", p.read_text(encoding="utf-8", errors="replace"))]
        if users:
            r.detail = f"Architecture tests ({len(users)} file(s)) run in the tests check."
        else:
            r.status, r.detail = "skipped", "No ArchUnitNET/NetArchTest rules in the repo; add them so layering is enforced (see the quality-gate skill)."
        return
    if g.node:
        configs = [n for n in DEPCRUISE_CONFIGS if (g.root / n).exists()]
        if not configs:
            r.status, r.detail = "skipped", "No .dependency-cruiser config; add rules so layering is enforced."
            return
        dirs = " ".join(shlex.quote(d) for d in g.gate_config.get("sourceDirs", ["src"]))
        out = g.out / "depcruise.json"
        g.run("architecture", f"npx --no-install depcruise {dirs} --config {configs[0]} -T json > {shlex.quote(str(out))}")
        r.log = g.rel(g.log_path("architecture"))
        try:
            summary = json.loads(out.read_text(encoding="utf-8")).get("summary", {})
        except (OSError, json.JSONDecodeError):
            r.status, r.detail = "fail", "dependency-cruiser produced no JSON:\n" + g.tail(g.log_path("architecture"), 20)
            return
        errors = summary.get("error", 0)
        r.metric = f"{errors} violation(s)"
        if errors:
            violations = [f"{v.get('from')} -> {v.get('to')} ({(v.get('rule') or {}).get('name')})" for v in summary.get("violations", [])
                          if (v.get("rule") or {}).get("severity") == "error"]
            r.status, r.detail = "fail", "Dependency rules broken:\n" + "\n".join(violations[:30])
        return
    pyproject = g.root / "pyproject.toml"
    configured = (g.root / ".importlinter").exists() or (
        pyproject.exists() and re.search(r"^\[tool\.importlinter\]", pyproject.read_text(encoding="utf-8"), re.M) is not None)
    if not configured:
        r.status, r.detail = "skipped", "No import-linter contracts; add them so layering is enforced."
        return
    if not g.has_tool("lint-imports"):
        r.status, r.detail = "fail", "import-linter isn't installed (pip install import-linter)."
        return
    code = g.run("architecture", "lint-imports")
    r.log = g.rel(g.log_path("architecture"))
    if code != 0:
        r.status, r.detail = "fail", "Import contracts broken:\n" + g.tail(g.log_path("architecture"), 30)


def check_dead_code(g: Gate, r: Result) -> None:
    files = g.subject_files()
    override = g.command_override("deadCode")
    if override:
        code = g.run("dead-code", override)
        r.log = g.rel(g.log_path("dead-code"))
        if code != 0:
            r.status, r.detail = "fail", "Unused code:\n" + g.tail(g.log_path("dead-code"), 30)
        return
    if g.stack == "dotnet":
        editorconfig = g.root / ".editorconfig"
        text = editorconfig.read_text(encoding="utf-8", errors="replace") if editorconfig.exists() else ""
        if re.search(r"dotnet_diagnostic\.IDE0051\.severity\s*=\s*(warning|error)", text):
            r.detail = "IDE0051/IDE0052/IDE0060 run in the build check."
        else:
            r.status, r.detail = "skipped", "IDE0051/IDE0052/IDE0060 aren't enabled in .editorconfig; unused code isn't detected."
        return
    if not files:
        r.status, r.detail = "skipped", f"No changed {g.subject}."
        return
    hits = []
    if g.node:
        out = g.out / "knip.json"
        g.run("dead-code", f"npx --no-install knip --reporter json > {shlex.quote(str(out))}")
        r.log = g.rel(g.log_path("dead-code"))
        try:
            issues = json.loads(out.read_text(encoding="utf-8")).get("issues", [])
        except (OSError, json.JSONDecodeError):
            r.status, r.detail = "fail", "knip produced no JSON:\n" + g.tail(g.log_path("dead-code"), 20)
            return
        changed = set(g.changed)
        for issue in issues:
            path = issue.get("file")
            if path not in changed:
                continue
            for kind, items in issue.items():
                if kind == "file" or not isinstance(items, list):
                    continue
                for item in items:
                    name = item.get("name", item) if isinstance(item, dict) else item
                    line = item.get("line") if isinstance(item, dict) else None
                    if kind == "files":
                        if g.is_new_file(path):
                            hits.append(f"{path}: unused file (nothing imports it)")
                    elif line is None or g.touched_line(path, line):
                        hits.append(f"{path}{':' + str(line) if line else ''}: unused {kind}: {name}")
    else:
        if not g.has_tool("vulture"):
            r.status, r.detail = "fail", "vulture isn't installed (pip install vulture)."
            return
        whitelist = [w for w in ("vulture_whitelist.py", "whitelist.py") if (g.root / w).exists()]
        production = set(files)
        scope = [d for d in g.gate_config.get("sourceDirs", []) + g.gate_config.get("testDirs", []) if (g.root / d).exists()] or ["."]
        code = g.run("dead-code", ["vulture", *scope, *whitelist, "--min-confidence", "60",
                                   "--exclude", "mutants,.venv,venv,node_modules,.atdd,build,dist"])
        r.log = g.rel(g.log_path("dead-code"))
        if code not in (0, 3):
            r.status, r.detail = "fail", f"vulture failed (exit {code}):\n" + g.tail(g.log_path("dead-code"), 20)
            return
        for line in g.log_path("dead-code").read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"^(?P<file>[^:]+):(?P<line>\d+): (?P<message>.+)$", line)
            if match and match.group("file") in production and g.touched_line(match.group("file"), int(match.group("line"))):
                hits.append(line)
    r.metric = f"{len(hits)} unused item(s) in changed code"
    if hits:
        r.status, r.detail = "fail", "Unused code introduced by this change (delete it or use it):\n" + "\n".join(hits[:30])


def check_vulnerabilities(g: Gate, r: Result) -> None:
    manifests_changed = [p for p in g.changed if g.matches_any(Path(p).name, g.profile["manifests"])]
    override = g.command_override("vulnerabilities")
    found = False
    if override:
        found = g.run("vulnerabilities", override) != 0
    elif g.stack == "dotnet":
        solution = g.gate_config.get("solution", "")
        out = g.out / "vulnerable-packages.json"
        target = shlex.quote(solution) if solution else ""
        code = g.run("vulnerabilities", f"dotnet package list --project {target} --vulnerable --include-transitive --format json > {shlex.quote(str(out))}"
                     if solution else f"dotnet package list --vulnerable --include-transitive --format json > {shlex.quote(str(out))}")
        if code != 0:
            code = g.run("vulnerabilities", f"dotnet list {target} package --vulnerable --include-transitive --format json > {shlex.quote(str(out))}", append=True)
        try:
            data = json.loads(out.read_text(encoding="utf-8"))
            vulnerable = [p.get("id") for proj in data.get("projects", []) for fw in proj.get("frameworks", []) or []
                          for p in (fw.get("topLevelPackages") or []) + (fw.get("transitivePackages") or []) if p.get("vulnerabilities")]
            found = bool(vulnerable)
            r.metric = f"{len(set(vulnerable))} vulnerable package(s)"
        except (OSError, json.JSONDecodeError):
            r.status, r.detail = "fail", "Couldn't read the vulnerable-package report:\n" + g.tail(g.log_path("vulnerabilities"), 20)
            return
    elif g.node:
        found = g.run("vulnerabilities", node_audit_command(g)) != 0
    else:
        if not g.has_tool("pip-audit"):
            r.status, r.detail = "fail", "pip-audit isn't installed (pip install pip-audit)."
            return
        found = g.run("vulnerabilities", "pip-audit") != 0
    r.log = g.rel(g.log_path("vulnerabilities"))
    if not found:
        return
    if manifests_changed:
        r.status = "fail"
        r.detail = (f"Known-vulnerable dependencies, and this change touches dependency manifests "
                    f"({', '.join(manifests_changed)}):\n" + g.tail(g.log_path("vulnerabilities"), 25))
    else:
        r.status = "warn"
        r.detail = "Known-vulnerable dependencies exist, but this change didn't touch dependency manifests (pre-existing; report it to a human)."


def check_suppressions(g: Gate, r: Result) -> None:
    pattern = re.compile("|".join(g.profile["suppressions"] + list(g.gate_config.get("extraSuppressionPatterns", []))))
    hits = []
    for path in g.changed:
        if g.is_generated(path) or path.startswith(".atdd/"):
            continue
        for number, text in sorted(g.added_lines.get(path, {}).items()):
            if pattern.search(text):
                hits.append(f"{path}:{number}: {text.strip()[:120]}")
    log = g.log_path("suppressions")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(hits) + ("\n" if hits else ""), encoding="utf-8")
    r.log = g.rel(log)
    r.metric = f"{len(hits)} new suppression(s)"
    r.threshold = "0"
    if hits:
        r.status = "fail"
        r.detail = "New suppressions, skips or ignores in changed code; fix the underlying issue instead:\n" + "\n".join(hits[:30])


def check_locked_files(g: Gate, r: Result) -> None:
    locked = g.config.get("lockedPaths", [])
    if not locked:
        r.status, r.detail = "fail", "lockedPaths is empty in .claude/atdd.json."
        return
    since = g.scenarios_commit
    if not since:
        features = str(g.config.get("featuresDir", "")).strip("/")
        if features:
            since = git(g.root, "log", "-1", "--format=%H", "--", f":(glob){features}/**/*.feature",
                        f":(glob){features}/**/requirements.md").strip()
    if not since:
        r.status, r.detail = "skipped", "No commit holding approved scenarios found (set ATDD_SCENARIOS_COMMIT)."
        return
    committed = git(g.root, "diff", "--name-only", f"{since}..HEAD").splitlines()
    dirty = git(g.root, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
    paths = sorted(set(committed) | {line[3:].split(" -> ")[-1] for line in dirty if len(line) > 3})

    def is_locked(path: str) -> bool:
        for raw in locked:
            pattern = raw[2:] if raw.startswith("./") else raw
            options = {pattern, pattern[3:] if pattern.startswith("**/") else pattern,
                       pattern[:-3] if pattern.endswith("/**") else pattern}
            if any(fnmatch.fnmatchcase(path, p) for p in options):
                return True
        return False

    hits = [p for p in paths if is_locked(p)]
    r.metric = f"{len(hits)} locked file(s) changed since {since[:10]}"
    r.threshold = "0"
    if hits:
        r.status = "fail"
        r.detail = "Locked files changed after the scenarios were approved (revert them; only humans change these):\n" + "\n".join(hits)


class GateError(Exception):
    """A check can't measure what it should: the check fails with this message."""


def run_node_tests(g: Gate, with_coverage: bool) -> int:
    """Unit and acceptance tests. In full mode every place the code runs records V8 coverage under <out>/coverage
    (see scripts/atdd/coverage.mjs), which then merges it into one Cobertura report."""
    commands = [c for c in (g.configured_command("unitTests"), g.configured_command("acceptanceTests")) if c] or ["npm test"]
    coverage = g.out / "coverage"
    merger = g.root / "scripts" / "atdd" / "coverage.mjs"
    if with_coverage and not merger.exists():
        g.log_path("tests").write_text("scripts/atdd/coverage.mjs is missing, so coverage can't be measured; run /atdd:init.\n",
                                       encoding="utf-8")
        return 1
    code = 0
    for index, command in enumerate(commands):
        env = {"ATDD_COVERAGE_DIR": str(coverage) if with_coverage else ""}
        if with_coverage and g.stack == "typescript" and not re.search(r"\bvitest\b", command):
            env["NODE_V8_COVERAGE"] = str(coverage / "node-v8")
        code = max(code, g.run("tests", command, append=index > 0, env=env))
    if with_coverage:
        code = max(code, g.run("tests", "node scripts/atdd/coverage.mjs merge", append=True,
                               env={"ATDD_COVERAGE_DIR": str(coverage)}))
        g.coverage_reports = [str(coverage / "merged" / "cobertura-coverage.xml")]
        g.branches_measured = True
    return code


def node_audit_command(g: Gate) -> str:
    if g.stack == "nextjs" and (g.root / "pnpm-lock.yaml").exists():
        return "pnpm audit --prod --audit-level high"
    return "npm audit --omit=dev --audit-level=high"


# ---------- NPath ----------

def check_npath(g: Gate, r: Result) -> None:
    files = g.subject_files()
    limit = g.thresholds["npath"]
    r.threshold = f"≤ {limit} per function"
    override = g.command_override("npath")
    if override:
        code = g.run("npath", override, env={"ATDD_NPATH": str(limit)})
        r.log = g.rel(g.log_path("npath"))
        if code != 0:
            r.status, r.detail = "fail", f"Functions over NPath {limit}:\n" + g.tail(g.log_path("npath"), 30)
        return
    if not files:
        r.status, r.detail = "skipped", f"No changed {g.subject}."
        return
    try:
        if g.node:
            measured = node_npaths(g, files, limit)
        elif g.stack == "python":
            measured = python_npaths(g, files)
        else:
            measured = [{"file": row["File"], "name": row["Name"], "start": row["Start"], "end": row["End"],
                         "npath": int(row["NPath"])} for row in run_dotnet_quality(g, "npath", files, "npath")]
    except GateError as error:
        r.status, r.detail = "fail", str(error)
        return
    over = [f"{m['file']}:{m['start']}-{m['end']} {m['name']}: NPath {m['npath']} > {limit}" for m in measured
            if m["npath"] > limit and g.touched(m["file"], m["start"], m["end"])]
    log = g.log_path("npath")
    if not log.exists():
        log.write_text("\n".join(over) + ("\n" if over else ""), encoding="utf-8")
    r.log = g.rel(log)
    r.metric = f"{len(over)} changed function(s) over {limit}"
    if over:
        r.status = "fail"
        r.detail = (f"Changed functions with more than {limit} acyclic execution paths (NPath). Sequential decisions "
                    "multiply, so split the function where independent decisions follow each other:\n" + "\n".join(over[:30]))


def node_npaths(g: Gate, files: list, limit: int) -> list:
    config = g.root / "scripts" / "atdd" / "eslint.quality.mjs"
    plugin = g.root / "scripts" / "atdd" / "eslint-npath.mjs"
    if not plugin.exists() or not config.exists() or "eslint-npath" not in config.read_text(encoding="utf-8"):
        raise GateError("scripts/atdd/eslint.quality.mjs doesn't load the NPath rule from scripts/atdd/eslint-npath.mjs; "
                        "copy both from the atdd quality-gate templates (/atdd:init).")
    report = g.out / "npath.json"
    quoted = " ".join(shlex.quote(f) for f in files)
    code = g.run("npath", f"npx --no-install eslint -c {shlex.quote(str(config))} --no-warn-ignored -f json "
                          f"-o {shlex.quote(str(report))} {quoted}",
                 env={"ATDD_NPATH": str(limit), "ATDD_COGNITIVE": str(g.thresholds["cognitive"])})
    try:
        entries = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise GateError(f"ESLint produced no report (exit {code}):\n" + g.tail(g.log_path("npath"), 20)) from None
    measured = []
    for entry in entries:
        path = g.rel(Path(entry.get("filePath", "")))
        for message in entry.get("messages", []):
            if message.get("fatal"):
                raise GateError(f"{path}:{message.get('line')}: {message.get('message')} (ESLint can't parse it, so its "
                                "NPath isn't measured)")
            found = re.match(r"'(.*)' has an NPath complexity of (\d+)", message.get("message", ""))
            if message.get("ruleId") == "atdd/npath-complexity" and found:
                measured.append({"file": path, "name": found.group(1), "start": message.get("line", 0),
                                 "end": message.get("endLine") or message.get("line", 0), "npath": int(found.group(2))})
    return measured


AST_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def python_conditions(node) -> int:
    """C(expression): each `and`/`or` once, each conditional expression twice, each comprehension filter once."""
    if node is None or isinstance(node, AST_SCOPES):
        return 0
    count = 0
    if isinstance(node, ast.BoolOp):
        count += len(node.values) - 1
    elif isinstance(node, ast.IfExp):
        count += 2
    elif isinstance(node, ast.comprehension):
        count += len(node.ifs)
    return count + sum(python_conditions(child) for child in ast.iter_child_nodes(node))


def python_sequence(statements) -> int:
    product = 1
    for statement in statements:
        product *= python_paths(statement)
    return product


def python_paths(node) -> int:
    """NPath of one statement (Nejmeh, 1988, with the table PMD and Checkstyle document)."""
    if isinstance(node, ast.If):
        return python_sequence(node.body) + (python_sequence(node.orelse) if node.orelse else 1) + python_conditions(node.test)
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return (python_sequence(node.body) + python_conditions(node.iter) + 1) * python_sequence(node.orelse)
    if isinstance(node, ast.While):
        return (python_sequence(node.body) + python_conditions(node.test) + 1) * python_sequence(node.orelse)
    if isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
        handled = python_sequence(node.body) * python_sequence(node.orelse) + sum(python_sequence(h.body) for h in node.handlers)
        return handled * python_sequence(node.finalbody)
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return max(1, sum(python_conditions(item) for item in node.items)) * python_sequence(node.body)
    if isinstance(node, getattr(ast, "Match", ())):
        return python_match_paths(node)
    if isinstance(node, AST_SCOPES):
        return 1
    return max(1, python_conditions(node))


def python_match_paths(node) -> int:
    irrefutable = any(isinstance(case.pattern, ast.MatchAs) and case.pattern.pattern is None and case.guard is None
                      for case in node.cases)
    total = python_conditions(node.subject) + (0 if irrefutable else 1)
    for case in node.cases:
        alternatives = len(case.pattern.patterns) if isinstance(case.pattern, ast.MatchOr) else 1
        total += python_sequence(case.body) + python_conditions(case.guard) + alternatives - 1
    return total


def python_npaths(g: Gate, files: list) -> list:
    measured = []
    for path in files:
        measured.extend(python_functions(parse_python(g.root, path, "its NPath isn't measured"), path, ""))
    return measured


def python_functions(node, path: str, prefix: str) -> list:
    found = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = f"{prefix}{child.name}"
            found.append({"file": path, "name": name, "start": child.lineno, "end": child.end_lineno,
                          "npath": python_sequence(child.body)})
            found.extend(python_functions(child, path, f"{name}."))
        elif isinstance(child, ast.Lambda):
            found.append({"file": path, "name": f"{prefix}<lambda>", "start": child.lineno, "end": child.end_lineno,
                          "npath": max(1, python_conditions(child.body))})
            found.extend(python_functions(child, path, prefix))
        else:
            found.extend(python_functions(child, path, f"{prefix}{child.name}." if isinstance(child, ast.ClassDef) else prefix))
    return found


def run_dotnet_quality(g: Gate, command: str, files: list, log: str):
    """Runs scripts/atdd/dotnet-quality (Roslyn from the .NET SDK) over the files and returns its JSON."""
    project = g.root / "scripts" / "atdd" / "dotnet-quality"
    if not (project / "dotnet-quality.csproj").exists():
        raise GateError("scripts/atdd/dotnet-quality is missing; copy templates/dotnet/dotnet-quality (/atdd:init).")
    listing = g.out / f"dotnet-quality-{command}-files.txt"
    listing.write_text("\n".join(files) + "\n", encoding="utf-8")
    out = g.out / f"dotnet-quality-{command}.json"
    code = g.run(log, ["dotnet", "run", "--project", str(project), "-c", "Release", "--", command, str(listing), str(out)])
    if code != 0 or not out.exists():
        raise GateError(f"The C# quality helper failed (exit {code}):\n" + g.tail(g.log_path(log), 20))
    return json.loads(out.read_text(encoding="utf-8"))


# ---------- dependency cycles ----------

DEPCRUISE_CONFIGS = (".dependency-cruiser.cjs", ".dependency-cruiser.js", ".dependency-cruiser.mjs", ".dependency-cruiser.json")


def check_dependency_cycles(g: Gate, r: Result) -> None:
    r.threshold = "0 cycles between namespaces or between projects"
    override = g.command_override("dependencyCycles")
    if override:
        code = g.run("dependency-cycles", override)
        r.log = g.rel(g.log_path("dependency-cycles"))
        if code != 0:
            r.status, r.detail = "fail", "Dependency cycles:\n" + g.tail(g.log_path("dependency-cycles"), 30)
        return
    production = g.production_files()
    if not production:
        r.status, r.detail = "skipped", "No production code in sourceDirs."
        return
    try:
        builders = {"python": python_dependency_graphs, "dotnet": dotnet_dependency_graphs}
        namespaces, projects = builders.get(g.stack, node_dependency_graphs)(g, production)
    except GateError as error:
        r.status, r.detail = "fail", str(error)
        return
    cycles = [("namespace", members, path, namespaces) for members, path in find_cycles(namespaces)] + \
             [("project", members, path, projects) for members, path in find_cycles(projects)]
    report = []
    for kind, members, path, edges in cycles:
        hops = [edges[(a, b)] for a, b in zip(path, path[1:])]
        report.append({"kind": kind, "members": members, "cycle": path, "evidence": [text for _, text in hops],
                       "touchedByThisChange": any(file in g.changed for file, _ in hops)})
    (g.out / "dependency-cycles.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    log = g.log_path("dependency-cycles")
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"{len({n for edge in namespaces for n in edge})} namespace(s) with dependencies, "
                     f"{len({n for edge in projects for n in edge})} project(s) with dependencies\n")
    r.log = g.rel(log)
    found = {kind: sum(1 for c in report if c["kind"] == kind) for kind in ("namespace", "project")}
    r.metric = f"{found['namespace']} namespace cycle(s), {found['project']} project cycle(s)"
    if report:
        r.status = "fail"
        r.detail = ("Dependency cycles between namespaces or projects. Break each one by moving the shared piece into a "
                    "namespace both sides may depend on, or by reversing one dependency (full list: "
                    f"{g.rel(g.out / 'dependency-cycles.json')}):\n" + "\n".join(describe_cycle(c) for c in report[:15]))


def describe_cycle(cycle: dict) -> str:
    origin = "this change touches it" if cycle["touchedByThisChange"] else "already in the code base"
    lines = [f"{cycle['kind']} cycle ({origin}): " + " -> ".join(cycle["cycle"])]
    lines += [f"    {text}" for text in cycle["evidence"]]
    if len(cycle["members"]) > len(cycle["cycle"]) - 1:
        lines.append(f"    all {len(cycle['members'])} members: {', '.join(cycle['members'])}")
    return "\n".join(lines)


def add_edge(edges: dict, source: str, target: str, file: str, text: str) -> None:
    if source != target:
        edges.setdefault((source, target), (file, text))


def find_cycles(edges: dict) -> list:
    """Strongly connected components of more than one node (Tarjan), each with one shortest loop through it."""
    graph = {}
    for source, target in edges:
        graph.setdefault(source, set()).add(target)
        graph.setdefault(target, set())
    index, low, on_stack, stack, components = {}, {}, set(), [], []

    def visit(node):
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)

    for start in sorted(graph):
        if start in index:
            continue
        visit(start)
        work = [(start, iter(sorted(graph[start])))]
        while work:
            node, children = work[-1]
            child = next((c for c in children if c not in index or c in on_stack), None)
            if child is not None and child not in index:
                visit(child)
                work.append((child, iter(sorted(graph[child]))))
                continue
            if child is not None:
                low[node] = min(low[node], index[child])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component = []
                while not component or component[-1] != node:
                    component.append(stack.pop())
                    on_stack.discard(component[-1])
                if len(component) > 1:
                    components.append(sorted(component))
    return [(members, shortest_loop(members, graph)) for members in sorted(components)]


def shortest_loop(members: list, graph: dict) -> list:
    start, inside = members[0], set(members)
    previous = {start: None}
    queue = [start]
    for node in queue:
        for successor in sorted(graph[node] & inside):
            if successor == start:
                path = [node]
                while previous[path[-1]] is not None:
                    path.append(previous[path[-1]])
                return list(reversed(path)) + [start]
            if successor not in previous:
                previous[successor] = node
                queue.append(successor)
    return members + [start]


def owner_of(path: str, roots: set) -> str:
    """The nearest enclosing directory in roots ('.' for the repo root)."""
    folder = posixpath.dirname(path)
    while folder and folder not in roots:
        folder = posixpath.dirname(folder)
    return folder or "."


def tracked(g: Gate, *patterns: str) -> list:
    return sorted(set(git(g.root, "ls-files", "-co", "--exclude-standard", "--", *patterns).splitlines()))


def node_dependency_graphs(g: Gate, production: list):
    """Namespaces are directories; projects are the workspace packages (directories holding a package.json)."""
    dirs = [d.strip("/") for d in g.gate_config.get("sourceDirs", ["src"]) if (g.root / d.strip("/")).exists()]
    configs = [n for n in DEPCRUISE_CONFIGS if (g.root / n).exists()]
    config = configs[0] if configs else str(g.out / "depcruise-atdd.cjs")
    if not configs:
        ts_config = "tsConfig: { fileName: 'tsconfig.json' }, " if (g.root / "tsconfig.json").exists() else ""
        Path(config).write_text("module.exports = { options: { " + ts_config + "tsPreCompilationDeps: true, "
                                "doNotFollow: { path: 'node_modules' } } }\n", encoding="utf-8")
    out = g.out / "dependency-graph.json"
    g.run("dependency-cycles", f"npx --no-install depcruise {' '.join(shlex.quote(d) for d in dirs)} "
                               f"--config {shlex.quote(config)} --output-type json --output-to {shlex.quote(str(out))}")
    try:
        modules = json.loads(out.read_text(encoding="utf-8")).get("modules", [])
    except (OSError, json.JSONDecodeError):
        raise GateError("dependency-cruiser produced no module graph (npm i -D dependency-cruiser):\n"
                        + g.tail(g.log_path("dependency-cycles"), 20)) from None
    known = set(production)
    if not any(m.get("source") in known for m in modules):
        raise GateError("dependency-cruiser's module graph contains none of the production files in sourceDirs, so cycles "
                        "can't be checked; check its config's exclude/includeOnly options.")
    manifests = {path: json.loads((g.root / path).read_text(encoding="utf-8")) for path in tracked(g, "package.json", ":(glob)**/package.json")}
    roots = {posixpath.dirname(path) for path in manifests}
    namespaces, projects = {}, {}
    for module in modules:
        source = module.get("source", "")
        if source not in known:
            continue
        for dependency in module.get("dependencies", []):
            target = dependency.get("resolved", "")
            if target in known:
                text = f"{source} imports {target}"
                add_edge(namespaces, posixpath.dirname(source) or ".", posixpath.dirname(target) or ".", source, text)
                add_edge(projects, owner_of(source, roots), owner_of(target, roots), source, text)
    packages = {data.get("name"): posixpath.dirname(path) or "." for path, data in manifests.items() if data.get("name")}
    for path, data in manifests.items():
        declared = {name for kind in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
                    for name in (data.get(kind) or {})}
        for name in sorted(declared & set(packages)):
            add_edge(projects, posixpath.dirname(path) or ".", packages[name], path, f"{path} depends on {name}")
    return namespaces, projects


def python_module_paths(g: Gate, production: list) -> dict:
    """{importable module name: path} for production files, imported from their sourceDirs folder (or its parent when
    the folder is itself a package)."""
    import_roots = {}
    for folder in [d.strip("/") or "." for d in g.gate_config.get("sourceDirs", [])] or ["."]:
        import_roots[folder] = posixpath.dirname(folder) if (g.root / folder / "__init__.py").exists() else folder
    modules = {}
    for path in production:
        folder = next((f for f in sorted(import_roots, key=len, reverse=True)
                       if f == "." or path.startswith(f + "/")), ".")
        root = import_roots[folder]
        relative = path if root in ("", ".") else path[len(root) + 1:]
        name = relative[:-3].replace("/", ".")
        modules[name[: -len(".__init__")] if name.endswith(".__init__") else name] = path
    return modules


def python_dependency_graphs(g: Gate, production: list):
    """Namespaces are packages (a module belongs to the package it sits in); projects are the directories holding a
    pyproject.toml, setup.py or setup.cfg."""
    modules = python_module_paths(g, production)
    package_of = {name: python_package(name, path) for name, path in modules.items()}
    project_roots = {posixpath.dirname(p) for p in tracked(g, ":(glob)**/pyproject.toml", ":(glob)**/setup.py",
                                                            ":(glob)**/setup.cfg", "pyproject.toml", "setup.py", "setup.cfg")}
    namespaces, projects = {}, {}
    for name, path in sorted(modules.items()):
        tree = parse_python(g.root, path, "its imports aren't checked for cycles")
        for line, target in python_imports(tree, name, package_of[name], modules):
            text = f"{path}:{line} imports {target}"
            add_edge(namespaces, package_of[name], package_of[target], path, text)
            add_edge(projects, owner_of(path, project_roots), owner_of(modules[target], project_roots), path, text)
    return namespaces, projects


def python_imports(tree, module: str, package: str, modules: dict):
    """(line, imported module of this code base) for every import, wherever it sits: lazy and TYPE_CHECKING imports
    are dependencies too."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = longest_known_prefix(alias.name, modules)
                if target:
                    yield node.lineno, target
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = [] if package == "(top level)" else package.split(".")
                parent = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join(parent + ([node.module] if node.module else []))
            for alias in node.names:
                target = (f"{base}.{alias.name}" if f"{base}.{alias.name}" in modules else None) or longest_known_prefix(base, modules)
                if target and target != module:
                    yield node.lineno, target


def longest_known_prefix(name: str, modules: dict):
    parts = name.split(".")
    for size in range(len(parts), 0, -1):
        candidate = ".".join(parts[:size])
        if candidate in modules:
            return candidate
    return None


def dotnet_dependency_graphs(g: Gate, production: list):
    """Namespaces come from the Roslyn helper (a namespace depends on another when its code uses a type declared
    there); projects from the ProjectReference items of every project file."""
    namespaces, projects = {}, {}
    for dependency in run_dotnet_quality(g, "namespaces", production, "dependency-cycles").get("Dependencies", []):
        add_edge(namespaces, dependency["From"], dependency["To"], dependency["File"],
                 f"{dependency['File']}:{dependency['Line']} uses {dependency['Symbol']}")
    for project in tracked(g, "*.csproj", "*.fsproj", "*.vbproj"):
        try:
            root = ElementTree.parse(g.root / project).getroot()
        except (OSError, ElementTree.ParseError) as error:
            raise GateError(f"Can't read {project}: {error}") from None
        for element in root.iter():
            if element.tag.split("}")[-1] == "ProjectReference" and element.get("Include"):
                target = posixpath.normpath(posixpath.join(posixpath.dirname(project), element.get("Include").replace("\\", "/")))
                add_edge(projects, project, target, project, f"{project} references {target}")
    return namespaces, projects


# ---------- cover mode ----------

# Paths that are production code in practically every repo: test paths matching one of them would let a cover run
# change production code.
PRODUCTION_PROBES = ("Program.cs", "package.json", "pyproject.toml", "src/main.py", "src/app/page.tsx",
                     "src/App/Orders/OrderService.cs")

ACCEPTANCE_ONLY_PLUGIN = '''"""pytest plugin written by the atdd gate: keeps only the tests under ATDD_ACCEPTANCE_DIR, so
mutmut measures what the acceptance tests alone notice."""
import os

_DIR = os.environ.get("ATDD_ACCEPTANCE_DIR", "tests/acceptance").strip("/") + "/"


def pytest_collection_modifyitems(config, items):
    others = [item for item in items if not item.nodeid.startswith(_DIR)]
    if others:
        config.hook.pytest_deselected(items=others)
        items[:] = [item for item in items if item.nodeid.startswith(_DIR)]
'''

MUTMUT_DETECTED = {"killed", "timeout", "caught by type check", "segfault"}
MUTMUT_SURVIVED = {"survived", "suspicious"}


def cover_test_paths(config: dict) -> list:
    """Globs for the files a cover run may change: cover.testPaths, else every gate.testDirs folder plus featuresDir.
    scripts/atdd_guard.py in the atdd plugin resolves them the same way."""
    configured = (config.get("cover") or {}).get("testPaths")
    if configured:
        return [str(p).strip() for p in configured if str(p).strip()]
    folders = list((config.get("gate") or {}).get("testDirs", [])) + [config.get("featuresDir", "")]
    return sorted({f"{f}/**" for f in (str(d).strip().strip("/") for d in folders) if f and f != "."})


def path_matches(path: str, pattern: str) -> bool:
    """fnmatch, where * spans directories; "dir/" and "dir/**" also match the folder itself, "**/x" also matches x."""
    pattern = pattern[2:] if pattern.startswith("./") else pattern
    if pattern.endswith("/"):
        pattern += "**"
    candidates = {pattern}
    if pattern.startswith("**/"):
        candidates.add(pattern[3:])
    if pattern.endswith("/**"):
        candidates.add(pattern[:-3])
    return any(fnmatch.fnmatchcase(path, p) for p in candidates)


def touched_paths(root: Path, base: str) -> list:
    """Every path that differs from base, committed or not: added, modified, deleted, both sides of a rename, untracked."""
    tracked = git(root, "diff", "--name-only", "--no-renames", "-z", base).split("\0")
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    return sorted({p for p in tracked + untracked if p})


def check_production_untouched(g: Gate, r: Result) -> None:
    patterns = cover_test_paths(g.config)
    r.threshold = "0 files outside the test paths"
    if not patterns:
        r.status, r.detail = "fail", ("No test paths: set gate.testDirs (or cover.testPaths) in .claude/atdd.json, so the "
                                     "gate knows which files a cover run may change.")
        return
    broad = [p for p in patterns if any(path_matches(probe, p) for probe in PRODUCTION_PROBES)]
    if broad:
        r.status, r.detail = "fail", (f"These test paths would match production files too: {', '.join(broad)}. Narrow "
                                     "cover.testPaths or gate.testDirs in .claude/atdd.json.")
        return
    outside = [p for p in touched_paths(g.root, g.base) if not any(path_matches(p, pattern) for pattern in patterns)]
    log = g.log_path("production-untouched")
    log.write_text(f"test paths: {', '.join(patterns)}\n" + "".join(f"{p}\n" for p in outside), encoding="utf-8")
    r.log = g.rel(log)
    r.metric = f"{len(outside)} file(s) outside the test paths changed"
    if outside:
        r.status = "fail"
        r.detail = ("A cover run may only add or change tests, but these files outside the test paths "
                    f"({', '.join(patterns)}) differ from the base commit. Revert them, or delete them if this change "
                    "created them. Production code, configuration, build files and manifests stay exactly as they are; "
                    "a change there is a human's, made on the base branch:\n" + "\n".join(outside[:40]))


def line_ranges(numbers) -> str:
    ranges, start, previous = [], None, None
    for n in sorted(numbers):
        if start is None:
            start = previous = n
        elif n == previous + 1:
            previous = n
        else:
            ranges.append(str(start) if start == previous else f"{start}-{previous}")
            start = previous = n
    if start is not None:
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(ranges)


def percent(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 100.0


def check_acceptance_coverage(g: Gate, r: Result) -> None:
    r.threshold = "every target runs; each uncovered line or branch is judged"
    targets, problem = g.targets()
    if problem:
        r.status, r.detail = "fail", problem
        return
    try:
        reports, code = run_acceptance_with_coverage(g)
    except GateError as error:
        r.status, r.detail = "fail", str(error)
        return
    r.log = g.rel(g.log_path("acceptance-coverage"))
    if code != 0:
        r.status, r.detail = "fail", (f"The acceptance tests failed while their coverage was measured (exit {code}). If they "
                                     "passed in the tests check, they're flaky (time, order or shared state):\n"
                                     + g.tail(g.log_path("acceptance-coverage"), 25))
        return
    reports = [p for p in reports if Path(p).exists()]
    if not reports:
        r.status, r.detail = "fail", ("The acceptance tests wrote no coverage report:\n"
                                     + g.tail(g.log_path("acceptance-coverage"), 20))
        return
    measured = target_coverage(g, reports, set(targets))
    lines = sum(len(m["lines"]) for m in measured.values())
    lines_run = sum(1 for m in measured.values() for hits in m["lines"].values() if hits)
    branches = sum(total for m in measured.values() for _, total in m["branches"].values())
    branches_taken = sum(covered for m in measured.values() for covered, _ in m["branches"].values())
    uncovered, partial = [], []
    for path, data in sorted(measured.items()):
        idle = [n for n, hits in data["lines"].items() if not hits]
        if idle:
            uncovered.append(f"{path}: lines {line_ranges(idle)}")
        partial += [f"{path}:{n}: {covered} of {total} branches taken"
                    for n, (covered, total) in sorted(data["branches"].items()) if covered < total]
    (g.out / "target-coverage.json").write_text(json.dumps(
        {path: {"lines": len(d["lines"]), "linesRun": sum(1 for h in d["lines"].values() if h),
                "uncoveredLines": sorted(n for n, h in d["lines"].items() if not h),
                "branches": sum(t for _, t in d["branches"].values()),
                "branchesTaken": sum(c for c, _ in d["branches"].values())} for path, d in sorted(measured.items())},
        indent=2) + "\n", encoding="utf-8")
    (g.out / "target-uncovered.txt").write_text("".join(f"{line}\n" for line in uncovered + partial), encoding="utf-8")
    r.metric = (f"{percent(lines_run, lines)}% of {lines} target line(s), "
                f"{percent(branches_taken, branches)}% of {branches} branch(es)")
    missing = [t for t in targets if t not in measured]
    never_run = [t for t in targets if t in measured and not any(measured[t]["lines"].values())]
    if missing:
        r.status, r.detail = "fail", ("These coverage targets are missing from the acceptance tests' coverage report: no "
                                     "scenario loads them, or coverage leaves them out (outside gate.sourceDirs?):\n"
                                     + "\n".join(missing))
    elif never_run:
        r.status, r.detail = "fail", ("No acceptance test runs a single line of these coverage targets. Either they don't "
                                     "implement the scenarios' behavior (drop them from the targets) or the step "
                                     "definitions go around them:\n" + "\n".join(never_run))
    elif uncovered or partial:
        r.status = "warn"
        r.detail = ("Target code the acceptance tests never run. For each gap: if a scenario claims that behavior, its "
                    "step definitions don't reach the real code path; if none does, it's behavior no scenario describes "
                    "yet (a spec gap for a human):\n" + "\n".join((uncovered + partial)[:40]))


def run_acceptance_with_coverage(g: Gate) -> tuple:
    """Runs the acceptance tests alone while recording coverage. Returns (report paths, exit code)."""
    out = g.out / "acceptance-coverage"
    out.mkdir(parents=True, exist_ok=True)
    acceptance = g.configured_command("acceptanceTests")
    override = g.command_override("acceptanceCoverage")
    if override:
        code = g.run("acceptance-coverage", override, env={"ATDD_OUT": str(g.out)})
        patterns = g.gate_config.get("acceptanceCoverageReports") or []
        patterns = patterns if isinstance(patterns, list) else [patterns]
        return sorted({p for pat in patterns for p in glob.glob(str(g.root / pat), recursive=True)}), code
    if g.stack == "dotnet":
        if not acceptance:
            raise GateError("Set commands.acceptanceTests (dotnet test <acceptance test project>) in .claude/atdd.json, "
                            "or gate.commands.acceptanceCoverage.")
        results = out / "test-results"
        code = g.run("acceptance-coverage", f"{acceptance} --collect:\"XPlat Code Coverage;Format=cobertura\" "
                                            f"--results-directory {shlex.quote(str(results))}")
        return sorted(glob.glob(str(results / "**" / "coverage.cobertura.xml"), recursive=True)), code
    if g.node:
        if not acceptance:
            raise GateError("Set commands.acceptanceTests in .claude/atdd.json, or gate.commands.acceptanceCoverage.")
        if not (g.root / "scripts" / "atdd" / "coverage.mjs").exists():
            raise GateError("scripts/atdd/coverage.mjs is missing, so coverage can't be measured; run /atdd:init.")
        env = {"ATDD_COVERAGE_DIR": str(out)}
        if g.stack == "typescript" and not re.search(r"\bvitest\b", acceptance):
            env["NODE_V8_COVERAGE"] = str(out / "node-v8")
        code = g.run("acceptance-coverage", acceptance, env=env)
        code = max(code, g.run("acceptance-coverage", "node scripts/atdd/coverage.mjs merge", append=True,
                               env={"ATDD_COVERAGE_DIR": str(out)}))
        return [str(out / "merged" / "cobertura-coverage.xml")], code
    report = out / "coverage.xml"
    base = g.command_override("pytest") or "python3 -m pytest --ignore=mutants"
    acceptance_dir = g.gate_config.get("acceptanceTestsDir", "tests/acceptance")
    cov = " ".join(f"--cov={shlex.quote(d)}" for d in g.gate_config.get("sourceDirs", [])) or "--cov=."
    code = g.run("acceptance-coverage", f"{base} {shlex.quote(acceptance_dir)} {cov} --cov-branch "
                                        f"--cov-report=xml:{shlex.quote(str(report))}")
    return [str(report)], code


def target_coverage(g: Gate, reports: list, targets: set) -> dict:
    """{path: {"lines": {line: hits}, "branches": {line: (covered, total)}}} for the target files in Cobertura reports;
    a line counts as run when any report ran it, and its branches come from the report that took most of them."""
    measured = {}
    for report in reports:
        try:
            root = ElementTree.parse(report).getroot()
        except (OSError, ElementTree.ParseError):
            continue
        sources = [s.text.strip() for s in root.iter("source") if s.text and s.text.strip()]
        for cls in root.iter("class"):
            path = cobertura_path(g, cls.get("filename", ""), sources, targets)
            if path not in targets:
                continue
            data = measured.setdefault(path, {"lines": {}, "branches": {}})
            for line in cls.findall("./lines/line"):
                number = int(line.get("number", "0"))
                data["lines"][number] = max(data["lines"].get(number, 0), int(float(line.get("hits", "0"))))
                match = re.search(r"\((\d+)/(\d+)\)", line.get("condition-coverage", ""))
                if line.get("branch", "").lower() != "true" or not match or int(match.group(2)) == 0:
                    continue
                covered, total = int(match.group(1)), int(match.group(2))
                previous = data["branches"].get(number)
                if previous is None or covered * previous[1] > previous[0] * total:
                    data["branches"][number] = (covered, total)
    return measured


def check_acceptance_mutation(g: Gate, r: Result) -> None:
    r.threshold = "advisory: each mutant the acceptance tests miss is judged"
    targets, problem = g.targets()
    if problem:
        r.status, r.detail = "fail", problem
        return
    override = g.command_override("acceptanceMutation")
    runner = g.gate_config.get("acceptanceRunner", "playwright-bdd" if g.stack == "nextjs" else "cucumber")
    if g.node and runner == "playwright-bdd" and not override:
        r.status, r.detail = "skipped", ("The scenarios run in a browser against a production build, which would be rebuilt "
                                         "for every mutant. Set gate.commands.acceptanceMutation to measure it anyway.")
        return
    if g.stack == "python":
        python_acceptance_mutation(g, r, targets, override)
        return
    configured_report = g.gate_config.get("acceptanceMutationReport")
    if g.stack == "dotnet":
        solution = g.gate_config.get("solution", "")
        if not solution and not override:
            r.status, r.detail = "fail", "Set gate.solution in .claude/atdd.json so Stryker.NET knows what to mutate."
            return
        out = g.out / "acceptance-stryker"
        shutil.rmtree(out, ignore_errors=True)
        test_filter = g.gate_config.get("acceptanceTestFilter", "FullyQualifiedName~Acceptance")
        mutate = " ".join(f"-m {shlex.quote('**/' + Path(t).name)}" for t in targets)
        command = override or (
            f"dotnet stryker --solution {shlex.quote(solution)} {mutate} --test-case-filter {shlex.quote(test_filter)} "
            f"-r json -r cleartext -r progress -O {shlex.quote(str(out))}")
        report = out / "reports" / "mutation-report.json"
    else:
        acceptance = g.configured_command("acceptanceTests")
        if not acceptance and not override:
            r.status, r.detail = "fail", ("Set commands.acceptanceTests in .claude/atdd.json, or "
                                         "gate.commands.acceptanceMutation.")
            return
        report = g.root / g.gate_config.get("mutationReport", "reports/mutation/mutation.json")
        incremental = g.root / ".atdd" / "cache" / "stryker-acceptance-incremental.json"
        incremental.parent.mkdir(parents=True, exist_ok=True)
        # The command runner runs the whole acceptance suite per mutant. StrykerJS's Cucumber runner would be faster,
        # but 10.1 can't load its formatter in Cucumber 13, which splits the file: URL it passes at the colon.
        command = override or (
            f"npx --no-install stryker run --testRunner command --commandRunner.command {shlex.quote(acceptance)} "
            f"--mutate {shlex.quote(','.join(glob_literal(t) for t in targets))} --reporters json,clear-text,progress "
            f"--incremental --incrementalFile {shlex.quote(str(incremental))}")
    if configured_report:
        report = g.root / configured_report
    report.unlink(missing_ok=True)
    code = g.run("acceptance-mutation", command)
    r.log = g.rel(g.log_path("acceptance-mutation"))
    if not report.exists():
        r.status, r.detail = "fail", (f"Mutation testing didn't produce a report (exit {code}):\n"
                                     + g.tail(g.log_path("acceptance-mutation"), 25))
        return
    report_acceptance_mutants(g, r, stryker_target_mutants(report, set(targets)))


def stryker_target_mutants(report: Path, targets: set) -> dict:
    """Mutants in the target files of a mutation-testing-report-schema JSON (StrykerJS, Stryker.NET), on any line."""
    data = json.loads(report.read_text(encoding="utf-8"))
    summary = {"detected": 0, "survived": 0, "noCoverage": 0, "survivors": []}
    known = dict.fromkeys(targets)
    for path, entry in (data.get("files") or {}).items():
        normalized = normalize_report_path(path, known)
        if normalized not in targets:
            continue
        for mutant in entry.get("mutants", []):
            status = mutant.get("status")
            line = ((mutant.get("location") or {}).get("start") or {}).get("line")
            if status in ("Killed", "Timeout"):
                summary["detected"] += 1
            elif status in ("Survived", "NoCoverage"):
                summary["survived" if status == "Survived" else "noCoverage"] += 1
                summary["survivors"].append(f"{normalized}:{line}: {mutant.get('mutatorName')} -> "
                                            f"{str(mutant.get('replacement', '')).strip()[:80]} [{status}]")
    return summary


def python_acceptance_mutation(g: Gate, r: Result, targets: list, override: str | None) -> None:
    """mutmut over the target modules, with a pytest plugin that deselects every test outside acceptanceTestsDir."""
    if not override and not g.has_tool("mutmut"):
        r.status, r.detail = "fail", "mutmut isn't installed (pip install mutmut)."
        return
    plugin_dir = g.root / ".atdd" / "cache" / "acceptance-only"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "atdd_acceptance_only.py").write_text(ACCEPTANCE_ONLY_PLUGIN, encoding="utf-8")
    env = {
        "PYTHONPATH": os.pathsep.join(p for p in (str(plugin_dir), os.environ.get("PYTHONPATH", "")) if p),
        **appended_to_environment("PYTEST_ADDOPTS", "-p atdd_acceptance_only"),
        "ATDD_ACCEPTANCE_DIR": g.gate_config.get("acceptanceTestsDir", "tests/acceptance"),
    }
    modules = sorted({python_module(t) for t in targets})
    mutants = g.root / "mutants"
    # mutmut caches which tests cover which function, and its cache doesn't know about the plugin: start clean, and
    # leave nothing behind for a later build-mode run to reuse.
    shutil.rmtree(mutants, ignore_errors=True)
    try:
        code = g.run("acceptance-mutation", override or "mutmut run " + " ".join(shlex.quote(f"{m}.*") for m in modules),
                     env=env)
        r.log = g.rel(g.log_path("acceptance-mutation"))
        g.run("acceptance-mutation-results", "mutmut results --all true", env=env)
        statuses = {}
        for line in g.log_path("acceptance-mutation-results").read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"^\s*(\S+):\s*(.+?)\s*$", line)
            if match and any(match.group(1).startswith(f"{m}.") for m in modules):
                statuses[match.group(1)] = match.group(2)
        if code != 0 or not statuses:
            r.status, r.detail = "fail", (f"mutmut failed (exit {code}); the acceptance tests must pass on their own "
                                         "first:\n" + g.tail(g.log_path("acceptance-mutation"), 25))
            return
        survived = sorted(n for n, s in statuses.items() if s in MUTMUT_SURVIVED)
        untested = sorted(n for n, s in statuses.items() if s == "no tests")
        for name in (survived + untested)[:40]:
            g.run("acceptance-mutation-diffs", ["mutmut", "show", name], append=True, env=env)
        report_acceptance_mutants(g, r, {
            "detected": sum(1 for s in statuses.values() if s in MUTMUT_DETECTED),
            "survived": len(survived),
            "noCoverage": len(untested),
            "survivors": [f"{n} [Survived]" for n in survived] + [f"{n} [NoCoverage]" for n in untested],
        }, diffs=g.log_path("acceptance-mutation-diffs"))
    finally:
        shutil.rmtree(mutants, ignore_errors=True)


def report_acceptance_mutants(g: Gate, r: Result, summary: dict, diffs: Path | None = None) -> None:
    survivors = summary["survivors"]
    (g.out / "acceptance-mutation-survivors.txt").write_text("".join(f"{s}\n" for s in survivors), encoding="utf-8")
    run = summary["detected"] + summary["survived"]
    if run + summary["noCoverage"] == 0:
        r.status, r.detail = "skipped", "No mutants in the coverage targets (only declarations, or nothing mutable)."
        return
    r.metric = (f"{summary['detected']} of {run} mutant(s) in code the scenarios run were noticed "
                f"({percent(summary['detected'], run)}%); {summary['noCoverage']} in code no scenario runs")
    if survivors:
        r.status = "warn"
        r.detail = ("Mutants the acceptance tests didn't notice. [Survived]: no assertion noticed the change (if the "
                    "line is also in target-uncovered.txt, no scenario even runs it). If a scenario's Then claims that "
                    "outcome, its step definition asserts too little; if none does, no scenario describes the behavior "
                    "(a spec gap for a human). [NoCoverage]: no scenario runs the code at all"
                    + (f". Each mutant's diff is in {g.rel(diffs)}" if diffs and diffs.exists() else "")
                    + ":\n" + "\n".join(survivors[:40]))


FLAKY_EXCLUDED_FROM = ("diff-coverage", "mutation")

CHECKS = {
    "production-untouched": check_production_untouched,
    "build": check_build,
    "format": check_format,
    "lint": check_lint,
    "tests": check_tests,
    "undefined-steps": check_undefined_steps,
    "duplicate-steps": check_duplicate_steps,
    "complexity": check_complexity,
    "cognitive": check_cognitive,
    "npath": check_npath,
    "dependency-cycles": check_dependency_cycles,
    "diff-coverage": check_diff_coverage,
    "mutation": check_mutation,
    "duplication": check_duplication,
    "architecture": check_architecture,
    "dead-code": check_dead_code,
    "vulnerabilities": check_vulnerabilities,
    "suppressions": check_suppressions,
    "locked-files": check_locked_files,
    "acceptance-coverage": check_acceptance_coverage,
    "acceptance-mutation": check_acceptance_mutation,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="ATDD quality gate")
    parser.add_argument("mode", choices=list(MODES))
    parser.add_argument("--version", action="version", version=f"atdd gate {GATE_VERSION}")
    parser.add_argument("--only", help="comma-separated subset of checks to run")
    parser.add_argument("--base", default=os.environ.get("ATDD_BASE"))
    parser.add_argument("--out", default=os.environ.get("ATDD_OUT"))
    parser.add_argument("--scenarios-commit", default=os.environ.get("ATDD_SCENARIOS_COMMIT"))
    parser.add_argument("--cover-targets", default=os.environ.get("ATDD_COVER_TARGETS"),
                        help="file listing the production files a cover run protects, one per line")
    args = parser.parse_args()

    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
    config_path = root / ".claude" / "atdd.json"
    if not config_path.exists():
        print("No .claude/atdd.json; run /atdd:init first.", file=sys.stderr)
        return 2
    config = json.loads(config_path.read_text(encoding="utf-8"))
    stack = config.get("stack", "")
    if stack not in STACKS:
        print(f"Unsupported stack '{stack}' in .claude/atdd.json (expected one of {', '.join(STACKS)}).", file=sys.stderr)
        return 2

    gate_config = config.get("gate") or {}
    out = Path(args.out) if args.out else root / ".atdd" / "quality" / "latest"
    if not out.is_absolute():
        out = root / out
    marker = out / ".atdd-gate-output"
    inside_workspace = (root / ".atdd").resolve() in out.resolve().parents
    if out.exists() and any(out.iterdir()):
        if not marker.exists() and not inside_workspace:
            print(f"{out} exists and isn't a gate output directory; refusing to clear it. Point ATDD_OUT elsewhere.", file=sys.stderr)
            return 2
        shutil.rmtree(out)
    (out / "logs").mkdir(parents=True, exist_ok=True)
    marker.write_text("Created by scripts/atdd/gate.py; cleared at the start of every run.\n", encoding="utf-8")

    base, base_note = resolve_base(root, config, args.base)
    if base_note:
        print(f"Base: {base_note}", flush=True)
    gate = Gate(root=root, config=config, mode=args.mode, base=base, out=out, scenarios_commit=args.scenarios_commit,
                stack=stack, profile=STACKS[stack], gate_config=gate_config,
                thresholds={**DEFAULT_THRESHOLDS, **(gate_config.get("thresholds") or {})},
                cover_targets_file=args.cover_targets)
    gate.changed = changed_files(root, base)
    gate.added_lines = added_lines(root, base, gate.changed)

    config_key, default_checks = MODES[args.mode]
    names = gate_config.get(config_key) or default_checks
    if args.only:
        names = [n.strip() for n in args.only.split(",") if n.strip()]
    unknown = [n for n in names if n not in CHECKS]
    if unknown:
        print(f"Unknown check(s): {', '.join(unknown)}. Known: {', '.join(CHECKS)}", file=sys.stderr)
        return 2
    if "diff-coverage" in names and "tests" not in names:
        names = ["tests"] + names

    results = []
    for name in names:
        result = Result(name=name)
        started = time.time()
        try:
            CHECKS[name](gate, result)
        except Exception as error:  # a crashing check is a failing check, never a silent pass
            result.status, result.detail = "fail", f"The check itself crashed: {error!r}"
        if name in FLAKY_EXCLUDED_FROM and gate.flaky_tests and result.status != "skipped":
            result.detail = "\n\n".join(filter(None, [
                "Measured without the tests classified flaky:\n" + "\n".join(gate.flaky_tests), result.detail]))
        result.seconds = round(time.time() - started, 1)
        results.append(result)
        print(f"[{result.status.upper():7}] {name} ({result.seconds}s){' - ' + result.metric if result.metric else ''}", flush=True)

    passed = all(r.status != "fail" for r in results)
    head = git(root, "rev-parse", "HEAD").strip()
    report = {
        "mode": args.mode,
        "stack": stack,
        "base": base,
        "baseNote": base_note,
        "head": head,
        "dirty": bool(git(root, "status", "--porcelain").strip()),
        "passed": passed,
        "thresholds": gate.thresholds,
        "changedFiles": gate.changed,
        "checks": [vars(r) for r in results],
    }
    if gate.cover:
        report["testPaths"] = cover_test_paths(config)
        report["coverTargets"] = gate.targets()[0] or []
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print()
    print(f"ATDD gate ({args.mode}) on {head[:10]} vs base {base[:10]}: {'PASS' if passed else 'FAIL'}")
    if base_note:
        print(f"Base: {base_note}")
    for r in results:
        if r.status in ("fail", "flaky", "warn"):
            print(f"\n## {r.name}: {r.status.upper()}" + (f" ({r.metric})" if r.metric else ""))
            print(r.detail.strip())
            if r.log:
                print(f"log: {r.log}")
    print(f"\nReport: {gate.rel(out / 'report.json')}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
