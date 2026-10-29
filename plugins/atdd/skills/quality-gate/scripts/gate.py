#!/usr/bin/env python3
"""ATDD quality gate: deterministic pass/fail checks over everything changed since the base commit.

    python3 scripts/atdd/gate.py fast    # commit gate: build, format, lint, tests, steps, complexity, suppressions
    python3 scripts/atdd/gate.py full    # adds diff coverage, mutation, duplication, architecture, dead code, ...
    python3 scripts/atdd/gate.py full --only mutation,diff-coverage

/atdd:init installs this script into each repo as scripts/atdd/gate.py, a locked file. Its source is
skills/quality-gate/scripts/gate.py in the atdd plugin; to update a repo, copy that over and commit it.

Configuration lives in .claude/atdd.json ("stack", "baseBranch", "featuresDir", "lockedPaths", "gate").
Environment overrides: ATDD_BASE (diff base, default: merge-base with baseBranch), ATDD_OUT (report
directory, default .atdd/quality/latest), ATDD_SCENARIOS_COMMIT (commit holding the approved scenarios,
for the locked-files check). Writes <out>/report.json plus one log per check; exits 1 if any check fails.

Standard library only; Python 3.9+. The tools it drives (lizard, diff-cover, jscpd, Stryker, ...) are
installed per repo, see the atdd quality-gate skill.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

JSCPD = "jscpd@5.4.2"

DEFAULT_THRESHOLDS = {
    "diffCoverage": 90,
    "mutationScore": 80,
    "cyclomatic": 10,
    "cognitive": 15,
    "functionLength": 60,
    "parameters": 5,
}

FAST_CHECKS = ["build", "format", "lint", "tests", "undefined-steps", "complexity", "cognitive", "suppressions"]
FULL_CHECKS = FAST_CHECKS + ["diff-coverage", "mutation", "duplication", "architecture", "dead-code",
                             "vulnerabilities", "locked-files"]

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
        "test_patterns": ["*.test.*", "*.spec.*", "*/__tests__/*", "test/*", "tests/*", "*/test/*", "*/tests/*",
                          "features/*", "*/features/*", "e2e/*"],
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


@dataclass
class Result:
    name: str
    status: str = "pass"          # pass | fail | warn | skipped
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
    _functions: dict | None = None

    # ---------- helpers ----------

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
        if self.stack == "typescript":
            files = [p for p in files if not p.endswith(".d.ts")]
        if include_tests:
            return files
        return [p for p in files if not self.is_test(p) and self.in_source_dirs(p)]

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


def resolve_base(root: Path, config: dict, override: str | None) -> str:
    if override:
        return git(root, "rev-parse", override).strip()
    branch = config.get("baseBranch", "main")
    for ref in (branch, f"origin/{branch}"):
        try:
            return git(root, "merge-base", ref, "HEAD").strip()
        except subprocess.CalledProcessError:
            continue
    raise SystemExit(f"Can't find the merge-base with '{branch}' or 'origin/{branch}'. Set ATDD_BASE or baseBranch in .claude/atdd.json.")


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
    if g.stack == "typescript":
        if not override and not (g.root / "tsconfig.json").exists():
            r.status, r.detail = "skipped", "No tsconfig.json; nothing to type-check."
            return
        code = g.run("build", override or "npx --no-install tsc --noEmit")
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
    elif g.stack == "typescript":
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
    if g.stack == "typescript":
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
            code = g.run("tests", f"dotnet test {target}--collect:\"XPlat Code Coverage\" --results-directory {shlex.quote(str(results))}")
            g.coverage_reports = sorted(glob.glob(str(results / "**" / "coverage.cobertura.xml"), recursive=True))
        else:
            code = g.run("tests", f"dotnet test {target}".strip())
    elif g.stack == "typescript":
        code = 0
        commands = [c for c in (g.configured_command("unitTests"), g.configured_command("acceptanceTests")) if c]
        if not commands:
            commands = ["npm test"]
        for index, command in enumerate(commands):
            if with_coverage:
                reports = g.out / f"coverage-{index}"
                include = " ".join(f"--include {shlex.quote(d.strip('/') + '/**')}" for d in g.gate_config.get("sourceDirs", ["src"]))
                command = (f"npx --no-install c8 --reporter=cobertura --reporter=text-summary --reports-dir {shlex.quote(str(reports))} "
                           f"--all {include} {command}")
                g.coverage_reports.append(str(reports / "cobertura-coverage.xml"))
            code = max(code, g.run("tests", command, append=index > 0))
    else:
        dirs = g.gate_config.get("sourceDirs", [])
        base = g.command_override("pytest") or "python3 -m pytest --ignore=mutants"
        if with_coverage:
            report = g.out / "coverage.xml"
            cov = " ".join(f"--cov={shlex.quote(d)}" for d in dirs) or "--cov=."
            code = g.run("tests", f"{base} {cov} --cov-branch --cov-report=xml:{shlex.quote(str(report))}")
            g.coverage_reports = [str(report)]
        else:
            code = g.run("tests", base)
    if code != 0:
        r.status, r.detail = "fail", "Tests failed:\n" + g.tail(g.log_path("tests"), 30)


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
    if g.stack == "typescript":
        runner = g.gate_config.get("acceptanceRunner", "cucumber")
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


def check_complexity(g: Gate, r: Result) -> None:
    files = g.code_files()
    t = g.thresholds
    r.threshold = f"CCN ≤ {t['cyclomatic']}, NLOC ≤ {t['functionLength']}, params ≤ {t['parameters']}"
    if not files:
        r.status, r.detail = "skipped", "No changed production code."
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
    files = g.code_files()
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
        r.status, r.detail = "skipped", "No changed production code."
        return
    quoted = " ".join(shlex.quote(f) for f in files)
    report = g.out / "cognitive.json"
    over = []
    if g.stack == "typescript":
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
    elif code != 0 or percent < limit:
        r.status = "fail"
        r.detail = f"Diff coverage {percent}% is below {limit}%. Uncovered changed lines:\n" + "\n".join(uncovered[:30])


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
    elif g.stack == "typescript":
        incremental = g.root / ".atdd" / "cache" / "stryker-incremental.json"
        incremental.parent.mkdir(parents=True, exist_ok=True)
        report = g.root / g.gate_config.get("mutationReport", "reports/mutation/mutation.json")
        report.unlink(missing_ok=True)
        command = override or (
            f"npx --no-install stryker run --mutate {shlex.quote(','.join(files))} --reporters json,clear-text,progress "
            f"--incremental --incrementalFile {shlex.quote(str(incremental))}")
        code = g.run("mutation", command)
    else:
        if not g.has_tool("mutmut"):
            r.status, r.detail = "fail", "mutmut isn't installed (pip install mutmut)."
            return
        modules = sorted({python_module(f) for f in files})
        command = override or "mutmut run " + " ".join(shlex.quote(f"{m}.*") for m in modules)
        code = g.run("mutation", command)
        r.log = g.rel(g.log_path("mutation"))
        g.run("mutation", "mutmut export-cicd-stats", append=True)
        g.run("mutation-results", "mutmut results")
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
    if g.stack == "typescript":
        configs = [n for n in (".dependency-cruiser.cjs", ".dependency-cruiser.js", ".dependency-cruiser.mjs", ".dependency-cruiser.json") if (g.root / n).exists()]
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
    files = g.code_files()
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
        r.status, r.detail = "skipped", "No changed production code."
        return
    hits = []
    if g.stack == "typescript":
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
    elif g.stack == "typescript":
        found = g.run("vulnerabilities", "npm audit --omit=dev --audit-level=high") != 0
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


CHECKS = {
    "build": check_build,
    "format": check_format,
    "lint": check_lint,
    "tests": check_tests,
    "undefined-steps": check_undefined_steps,
    "complexity": check_complexity,
    "cognitive": check_cognitive,
    "diff-coverage": check_diff_coverage,
    "mutation": check_mutation,
    "duplication": check_duplication,
    "architecture": check_architecture,
    "dead-code": check_dead_code,
    "vulnerabilities": check_vulnerabilities,
    "suppressions": check_suppressions,
    "locked-files": check_locked_files,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="ATDD quality gate")
    parser.add_argument("mode", choices=["fast", "full"])
    parser.add_argument("--only", help="comma-separated subset of checks to run")
    parser.add_argument("--base", default=os.environ.get("ATDD_BASE"))
    parser.add_argument("--out", default=os.environ.get("ATDD_OUT"))
    parser.add_argument("--scenarios-commit", default=os.environ.get("ATDD_SCENARIOS_COMMIT"))
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

    base = resolve_base(root, config, args.base)
    gate = Gate(root=root, config=config, mode=args.mode, base=base, out=out, scenarios_commit=args.scenarios_commit,
                stack=stack, profile=STACKS[stack], gate_config=gate_config,
                thresholds={**DEFAULT_THRESHOLDS, **(gate_config.get("thresholds") or {})})
    gate.changed = changed_files(root, base)
    gate.added_lines = added_lines(root, base, gate.changed)

    names = gate_config.get(args.mode) or (FAST_CHECKS if args.mode == "fast" else FULL_CHECKS)
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
        result.seconds = round(time.time() - started, 1)
        results.append(result)
        print(f"[{result.status.upper():7}] {name} ({result.seconds}s){' - ' + result.metric if result.metric else ''}", flush=True)

    passed = all(r.status != "fail" for r in results)
    head = git(root, "rev-parse", "HEAD").strip()
    report = {
        "mode": args.mode,
        "stack": stack,
        "base": base,
        "head": head,
        "dirty": bool(git(root, "status", "--porcelain").strip()),
        "passed": passed,
        "thresholds": gate.thresholds,
        "changedFiles": gate.changed,
        "checks": [vars(r) for r in results],
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print()
    print(f"ATDD gate ({args.mode}) on {head[:10]} vs base {base[:10]}: {'PASS' if passed else 'FAIL'}")
    for r in results:
        if r.status in ("fail", "warn"):
            print(f"\n## {r.name}: {r.status.upper()}" + (f" ({r.metric})" if r.metric else ""))
            print(r.detail.strip())
            if r.log:
                print(f"log: {r.log}")
    print(f"\nReport: {gate.rel(out / 'report.json')}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
