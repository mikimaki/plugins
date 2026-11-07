#!/usr/bin/env python3
"""Deterministic checks for one issue's Gherkin: requirement coverage and scenario structure.

Usage:
    gherkin_check.py <feature-dir> [--files F ...] [--ref REF] [--ledger PATH] [--glossary PATH] [--json]
                     [--max-steps N] [--strict]

<feature-dir> holds requirements.md (the requirements ledger) and the issue's .feature files.
--files checks only the named .feature files (names inside <feature-dir>, or paths): full structure checks on them,
and every requirement tag they use must exist in the ledger. Requirements that none of them covers are reported as
information, not as errors, because the area's other files cover them (/atdd:cover-queue covers one file at a time).
--ref reads everything from a git commit, branch or tag of the repo in the current directory instead of the working
tree; <feature-dir> and the other paths are then relative to the repo root.
The glossary defaults to docs.glossary in the repo's .claude/atdd.json, else GLOSSARY.md at the repo root.
UI words that are part of a glossary term (a domain noun that happens to contain "card" or "page") aren't flagged.
Exit codes: 0 no errors (warnings allowed unless --strict), 1 errors found, 2 usage or I/O problem.
Standard library only, so it runs anywhere Python 3.9+ does.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

REQ_LINE = re.compile(r"^\s*[-*]\s*\**(R\d+)\**\s*[:.–—-]\s*(.+?)\s*$")
REQ_TAG = re.compile(r"^@R\d+$")
PLACEHOLDER = re.compile(r"<([A-Za-z_][\w .-]*)>")
HEADING = re.compile(r"^\s*#{1,6}\s*(.+?)\s*$")
NONE_ITEM = re.compile(r"^(none|n/a|-)\.?$", re.IGNORECASE)

STEP_KEYWORDS = ("Given ", "When ", "Then ", "And ", "But ", "* ")
SCENARIO_KEYWORDS = ("Scenario Outline:", "Scenario Template:", "Scenario:", "Example:")
EXAMPLES_KEYWORDS = ("Examples:", "Scenarios:")

UI_OR_TECH_WORDS = re.compile(
    r"\b(click(s|ed|ing)?|tap(s|ped|ping)?|press(es|ed|ing)?|types? .{0,30}?\binto|enters? .{0,30}?\binto|"
    r"selects? .{0,30}?\bfrom|fills? in|navigates? to|scroll(s|ed|ing)?|hover(s|ed|ing)?|"
    r"buttons?|checkbox(es)?|drop-?downs?|text ?box(es)?|fields?|cards?|banners?|toasts?|modals?|dialogs?|"
    r"pop-?ups?|pages?|screens?|pre-?filled|"
    r"url|endpoint|http|status code|json|payload|request body|database|sql|xpath|css selector|"
    r"get|post|put|patch|delete)\b"
)
QUOTED = re.compile(r'"[^"]*"')
GLOSSARY_TERM = re.compile(
    r"^\s*(?:[-*+]\s+|\d+\.\s+)?(?:\*\*|__)(?P<bold>[^*_\n]+?)(?:\*\*|__)"
    r"|^\s*#{2,6}\s+(?P<heading>[^\n]+?)\s*$"
    r"|^\s*\|\s*(?P<cell>[^|\n]+?)\s*\|"
    r"|^\s*[-*+]\s+(?P<plain>[A-Za-z][\w '/-]{0,60}?)\s*:",
    re.MULTILINE,
)
HTTP_VERBS = {"get", "post", "put", "patch", "delete"}
VAGUE_OUTCOME = re.compile(
    r"\b(as expected|correct(ly)?|proper(ly)?|appropriate(ly)?|successful(ly)?|works|valid|ok|fine)\b",
    re.IGNORECASE,
)


@dataclass
class Step:
    keyword: str
    kind: str
    text: str
    line: int
    table: list = field(default_factory=list)
    docstring: list = field(default_factory=list)


@dataclass
class Examples:
    tags: list
    line: int
    header: list = field(default_factory=list)
    rows: list = field(default_factory=list)


@dataclass
class Scenario:
    kind: str  # scenario | outline | background
    name: str
    line: int
    tags: list
    rule_tags: list
    steps: list = field(default_factory=list)
    examples: list = field(default_factory=list)


@dataclass
class Feature:
    path: Path
    name: str = ""
    line: int = 0
    tags: list = field(default_factory=list)
    scenarios: list = field(default_factory=list)


@dataclass
class Issue:
    level: str
    code: str
    file: str
    line: int
    message: str


class Report:
    def __init__(self) -> None:
        self.issues: list[Issue] = []

    def error(self, code: str, file: str, line: int, message: str) -> None:
        self.issues.append(Issue("error", code, file, line, message))

    def warn(self, code: str, file: str, line: int, message: str) -> None:
        self.issues.append(Issue("warning", code, file, line, message))

    def count(self, level: str) -> int:
        return sum(1 for i in self.issues if i.level == level)


class Disk:
    """Reads the feature directory from the working tree."""

    def is_dir(self, path: Path) -> bool:
        return path.is_dir()

    def is_file(self, path: Path) -> bool:
        return path.is_file()

    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def features(self, directory: Path) -> list:
        return sorted(directory.rglob("*.feature"))

    def repo_root(self, start: Path) -> Path | None:
        for directory in [start.resolve(), *start.resolve().parents]:
            if (directory / ".claude" / "atdd.json").is_file() or (directory / ".git").exists():
                return directory
        return None


class GitRef:
    """Reads the feature directory from a git commit, branch or tag, never from the working tree. Paths are
    relative to the repo root."""

    def __init__(self, ref: str, cwd: Path) -> None:
        self.ref = ref
        self.cwd = cwd

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self.cwd), *args], capture_output=True, text=True)

    def _spec(self, path: Path) -> str:
        return f"{self.ref}:{PurePosixPath(path.as_posix())}".rstrip("/")

    def valid(self) -> bool:
        return self._git("rev-parse", "--verify", "--quiet", f"{self.ref}^{{commit}}").returncode == 0

    def is_dir(self, path: Path) -> bool:
        result = self._git("cat-file", "-t", self._spec(path))
        return result.returncode == 0 and result.stdout.strip() == "tree"

    def is_file(self, path: Path) -> bool:
        result = self._git("cat-file", "-t", self._spec(path))
        return result.returncode == 0 and result.stdout.strip() == "blob"

    def read(self, path: Path) -> str:
        result = self._git("show", self._spec(path))
        if result.returncode != 0:
            raise OSError(result.stderr.strip() or f"{self._spec(path)} doesn't exist")
        return result.stdout

    def features(self, directory: Path) -> list:
        listing = self._git("ls-tree", "-r", "--name-only", self.ref, "--", directory.as_posix().rstrip("/") + "/")
        return sorted(Path(p) for p in listing.stdout.splitlines() if p.endswith(".feature"))

    def repo_root(self, start: Path) -> Path | None:
        return Path(".")


def split_cells(line: str) -> list:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells, current, escaped = [], "", False
    for ch in body:
        if escaped:
            current += ch
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == "|":
            cells.append(current.strip())
            current = ""
        else:
            current += ch
    cells.append(current.strip())
    return cells


def parse_feature(path: Path, text: str, rel: str, report: Report) -> Feature | None:
    feature = Feature(path=path)
    pending_tags: list = []
    rule_tags: list = []
    container = None  # Scenario or Examples currently receiving lines
    current_scenario: Scenario | None = None
    current_step: Step | None = None
    last_kind = None
    in_docstring = None
    seen_feature = False

    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()

        if in_docstring:
            if stripped.startswith(in_docstring):
                in_docstring = None
            elif current_step:
                current_step.docstring.append(stripped)
            continue

        if not stripped:
            continue
        if stripped.startswith("#"):
            match = re.match(r"#\s*language\s*:\s*(\S+)", stripped)
            if match and match.group(1).lower() not in ("en", "en-us", "en-gb"):
                report.error("unsupported-language", rel, number,
                             f"Only English keywords are checked; found language '{match.group(1)}'.")
                return None
            continue

        if stripped.startswith("@"):
            tags = stripped.split("#")[0].split()
            for tag in tags:
                if not tag.startswith("@") or len(tag) == 1:
                    report.error("malformed-tag", rel, number, f"Malformed tag '{tag}'.")
            pending_tags.extend(t for t in tags if t.startswith("@") and len(t) > 1)
            continue

        if stripped.startswith("Feature:"):
            if seen_feature:
                report.error("multiple-features", rel, number, "Only one Feature per file.")
            seen_feature = True
            feature.name = stripped[len("Feature:"):].strip()
            feature.line = number
            feature.tags = pending_tags
            pending_tags = []
            container = current_scenario = current_step = None
            continue

        if not seen_feature:
            report.error("content-before-feature", rel, number, f"Unexpected line before 'Feature:': {stripped!r}")
            continue

        if stripped.startswith("Rule:"):
            rule_tags = pending_tags
            pending_tags = []
            container = current_scenario = current_step = None
            continue

        if stripped.startswith("Background:"):
            if pending_tags:
                report.error("tags-on-background", rel, number, "Background can't carry tags.")
                pending_tags = []
            current_scenario = Scenario("background", stripped[len("Background:"):].strip(), number, [], list(rule_tags))
            feature.scenarios.append(current_scenario)
            container, current_step, last_kind = current_scenario, None, None
            continue

        keyword = next((k for k in SCENARIO_KEYWORDS if stripped.startswith(k)), None)
        if keyword:
            kind = "outline" if keyword in ("Scenario Outline:", "Scenario Template:") else "scenario"
            current_scenario = Scenario(kind, stripped[len(keyword):].strip(), number, pending_tags, list(rule_tags))
            feature.scenarios.append(current_scenario)
            pending_tags = []
            container, current_step, last_kind = current_scenario, None, None
            continue

        keyword = next((k for k in EXAMPLES_KEYWORDS if stripped.startswith(k)), None)
        if keyword:
            if not current_scenario or current_scenario.kind == "background":
                report.error("examples-without-scenario", rel, number, "Examples must follow a Scenario Outline.")
                pending_tags = []
                continue
            if current_scenario.kind != "outline":
                report.error("examples-on-plain-scenario", rel, number,
                             f"'{current_scenario.name}' has Examples but isn't a Scenario Outline.")
            examples = Examples(pending_tags, number)
            current_scenario.examples.append(examples)
            pending_tags = []
            container, current_step = examples, None
            continue

        step_keyword = next((k for k in STEP_KEYWORDS if stripped.startswith(k)), None)
        if step_keyword:
            if pending_tags:
                report.error("tags-on-step", rel, number, "Tags must precede a Feature, Rule, Scenario or Examples.")
                pending_tags = []
            if not isinstance(container, Scenario):
                report.error("step-outside-scenario", rel, number, f"Step outside a Scenario or Background: {stripped!r}")
                continue
            word = step_keyword.strip()
            if word in ("And", "But", "*"):
                if last_kind is None:
                    report.error("leading-conjunction", rel, number,
                                 f"'{current_scenario.name or 'Background'}' starts with '{word}'; start with Given, When or Then.")
                    kind = "given"
                else:
                    kind = last_kind
            else:
                kind = word.lower()
            current_step = Step(word, kind, stripped[len(step_keyword):].strip(), number)
            current_scenario.steps.append(current_step)
            last_kind = kind
            continue

        if stripped.startswith("|"):
            cells = split_cells(stripped)
            if isinstance(container, Examples):
                if not container.header:
                    container.header = cells
                else:
                    if len(cells) != len(container.header):
                        report.error("ragged-table", rel, number, "Examples row has a different number of cells than its header.")
                    container.rows.append(cells)
            elif current_step:
                current_step.table.append(cells)
            else:
                report.error("orphan-table", rel, number, "Table without a step or Examples above it.")
            continue

        if stripped.startswith('"""') or stripped.startswith("```"):
            if not current_step:
                report.error("orphan-docstring", rel, number, "Doc string without a step above it.")
            in_docstring = stripped[:3]
            continue

        if current_step is not None:
            report.error("unexpected-text", rel, number,
                         f"Free text after a step (missing keyword?): {stripped!r}")
        # Otherwise it's a description line under Feature/Rule/Scenario/Examples, which Gherkin allows.

    if in_docstring:
        report.error("unclosed-docstring", rel, len(text.splitlines()), "Doc string is never closed.")
    if not seen_feature:
        report.error("no-feature", rel, 1, "File has no 'Feature:' line.")
        return None
    return feature


def parse_ledger(text: str, report: Report, rel: str) -> tuple[dict, list]:
    requirements: dict = {}
    open_questions: list = []
    section = None
    for number, raw in enumerate(text.splitlines(), start=1):
        heading = HEADING.match(raw)
        if heading:
            title = heading.group(1).lower()
            section = "open" if "open question" in title else ("out" if "out of scope" in title else "body")
            continue
        if section == "open":
            item = re.match(r"^[-*]\s+(.+?)\s*$", raw)
            if item and not NONE_ITEM.match(item.group(1)):
                open_questions.append(item.group(1))
            continue
        if section == "out":
            continue
        match = REQ_LINE.match(raw)
        if match:
            rid = match.group(1)
            if rid in requirements:
                report.error("duplicate-requirement", rel, number, f"{rid} is defined twice in the ledger.")
            requirements[rid] = {"text": match.group(2), "line": number}
    return requirements, open_questions


def placeholders_in(scenario: Scenario) -> set:
    found = set(PLACEHOLDER.findall(scenario.name))
    for step in scenario.steps:
        found.update(PLACEHOLDER.findall(step.text))
        for row in step.table:
            for cell in row:
                found.update(PLACEHOLDER.findall(cell))
        for line in step.docstring:
            found.update(PLACEHOLDER.findall(line))
    return found


def check_structure(feature: Feature, rel: str, report: Report, max_steps: int, names: dict,
                    domain_terms: re.Pattern | None) -> None:
    if not feature.name:
        report.error("unnamed-feature", rel, feature.line, "Feature has no name.")
    if not any(t.lower().startswith("@issue-") for t in feature.tags):
        report.warn("missing-issue-tag", rel, feature.line, "Feature has no @issue-<key> tag.")
    for tag in feature.tags:
        if REQ_TAG.match(tag):
            report.warn("requirement-tag-on-feature", rel, feature.line,
                        f"{tag} on the Feature line is too coarse to show coverage; tag the scenarios that verify it.")

    real = [s for s in feature.scenarios if s.kind != "background"]
    if not real:
        report.error("no-scenarios", rel, feature.line, "Feature has no scenarios.")

    for scenario in feature.scenarios:
        label = scenario.name or "Background"
        if scenario.kind == "background":
            if len(scenario.steps) > 4:
                report.warn("long-background", rel, scenario.line,
                            f"Background has {len(scenario.steps)} steps; keep only context every scenario needs (≤4).")
            if any(s.kind != "given" for s in scenario.steps):
                report.error("background-not-given", rel, scenario.line, "Background may only contain Given steps.")
            continue

        if not scenario.name:
            report.error("unnamed-scenario", rel, scenario.line, "Scenario has no name.")
        else:
            key = scenario.name.strip().lower()
            if key in names:
                report.error("duplicate-scenario-name", rel, scenario.line,
                             f"Scenario name '{scenario.name}' is also used at {names[key]}.")
            else:
                names[key] = f"{rel}:{scenario.line}"

        kinds = [s.kind for s in scenario.steps]
        if not scenario.steps:
            report.error("empty-scenario", rel, scenario.line, f"'{label}' has no steps.")
            continue
        if "then" not in kinds:
            report.error("no-then", rel, scenario.line, f"'{label}' has no Then step, so it verifies nothing.")
        if "when" not in kinds:
            report.warn("no-when", rel, scenario.line, f"'{label}' has no When step; name the action or event that triggers the outcome.")
        order = {"given": 0, "when": 1, "then": 2}
        for previous, current in zip(scenario.steps, scenario.steps[1:]):
            if order[current.kind] < order[previous.kind]:
                report.error("step-order", rel, current.line,
                             f"'{label}' has a {current.kind.capitalize()} step after a {previous.kind.capitalize()} step; "
                             "one scenario verifies one behavior, in Given → When → Then order.")
                break
        when_count = kinds.count("when")
        if when_count > 1:
            report.warn("multiple-actions", rel, scenario.line,
                        f"'{label}' has {when_count} When steps; a scenario should have one trigger.")
        if len(scenario.steps) > max_steps:
            report.warn("long-scenario", rel, scenario.line,
                        f"'{label}' has {len(scenario.steps)} steps (limit {max_steps}).")
        seen_steps = set()
        for step in scenario.steps:
            text_key = step.text.lower()
            if text_key in seen_steps:
                report.warn("duplicate-step", rel, step.line, f"Repeated step in '{label}': {step.text!r}")
            seen_steps.add(text_key)
            unquoted = QUOTED.sub('""', step.text)
            if domain_terms:
                unquoted = domain_terms.sub(" ", unquoted)
            for match in UI_OR_TECH_WORDS.finditer(unquoted.lower()):
                word = match.group(0)
                if word in HTTP_VERBS and not re.search(rf"\b{word.upper()}\b", unquoted):
                    continue
                report.warn("imperative-or-technical-step", rel, step.line,
                            f"'{word}' in {step.keyword} {step.text!r} names UI mechanics, UI elements or transport; "
                            "describe the business behavior in domain terms (quoted user-visible text is fine) "
                            "unless the spec makes it part of the contract.")
                break
            if step.kind == "then" and VAGUE_OUTCOME.search(step.text):
                report.warn("vague-outcome", rel, step.line,
                            f"Outcome {step.text!r} is vague; state the observable result with concrete values.")

        used = placeholders_in(scenario)
        if scenario.kind == "outline":
            if not scenario.examples:
                report.error("outline-without-examples", rel, scenario.line, f"Outline '{label}' has no Examples.")
            for examples in scenario.examples:
                if not examples.header:
                    report.error("examples-without-table", rel, examples.line, f"Examples of '{label}' have no table.")
                    continue
                if not examples.rows:
                    report.error("examples-without-rows", rel, examples.line, f"Examples of '{label}' have no rows.")
                header = set(examples.header)
                for name in sorted(used - header):
                    report.error("undefined-placeholder", rel, scenario.line,
                                 f"'{label}' uses <{name}> but its Examples (line {examples.line}) have no '{name}' column.")
                for name in sorted(header - used):
                    report.error("unused-example-column", rel, examples.line,
                                 f"Examples column '{name}' is never used in '{label}'.")
        elif used:
            report.warn("placeholder-in-scenario", rel, scenario.line,
                        f"'{label}' contains <{sorted(used)[0]}> but isn't a Scenario Outline.")


def requirement_tags(scenario: Scenario) -> dict:
    """Map each @R tag to the Examples blocks it covers (None = the whole scenario)."""
    tags = {t: None for t in scenario.tags + scenario.rule_tags if REQ_TAG.match(t)}
    for examples in scenario.examples:
        for tag in examples.tags:
            if REQ_TAG.match(tag):
                tags.setdefault(tag, examples.line)
    return tags


def find_glossary(feature_dir: Path, source) -> Path | None:
    """The glossary named in the repo's .claude/atdd.json, else GLOSSARY.md at the repo root."""
    root = source.repo_root(feature_dir)
    if root is None:
        return None
    config = root / ".claude" / "atdd.json"
    docs = json.loads(source.read(config)).get("docs", {}) if source.is_file(config) else {}
    glossary = root / (docs.get("glossary") or "GLOSSARY.md")
    return glossary if source.is_file(glossary) else None


def domain_terms_pattern(glossary: Path | None, source) -> re.Pattern | None:
    """Glossary terms that contain a UI or transport word, as one pattern that also matches their plurals."""
    if not glossary:
        return None
    terms = set()
    for match in GLOSSARY_TERM.finditer(source.read(glossary)):
        term = next(group for group in match.groups() if group).strip().strip(":").strip()
        if term and UI_OR_TECH_WORDS.search(term.lower()):
            terms.add(term.lower())
    if not terms:
        return None
    alternatives = sorted((r"\s+".join(map(re.escape, t.split())) + "(?:e?s)?" for t in terms), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(alternatives) + r")\b", re.IGNORECASE)


def selected_files(feature_dir: Path, names: list, source) -> tuple[list, list]:
    """Resolve --files: a name inside <feature-dir>, or a path. Returns (paths, problems)."""
    paths, problems = [], []
    for name in names:
        candidates = [feature_dir / name, Path(name)]
        path = next((c for c in candidates if source.is_file(c)), None)
        if path is None:
            problems.append(f"{name}: no such file in {feature_dir}")
        elif path.suffix != ".feature":
            problems.append(f"{name}: not a .feature file")
        elif not path.resolve().is_relative_to(feature_dir.resolve()):
            problems.append(f"{name}: outside {feature_dir}, so its ledger isn't {feature_dir}/requirements.md")
        else:
            paths.append(path)
    return sorted(set(paths)), problems


def run(feature_dir: Path, ledger_path: Path, max_steps: int, glossary: Path | None, source=None,
        selection: list | None = None) -> dict:
    source = source or Disk()
    report = Report()
    root = feature_dir.parent
    domain_terms = domain_terms_pattern(glossary, source)

    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(root))
        except ValueError:
            return str(p)

    requirements, open_questions = {}, []
    if not source.is_file(ledger_path):
        report.error("missing-ledger", rel(ledger_path), 0,
                     "No requirements ledger; write requirements.md with one '- R<n>: <behavior>' line per requirement.")
    else:
        requirements, open_questions = parse_ledger(source.read(ledger_path), report, rel(ledger_path))
        if not requirements:
            report.error("empty-ledger", rel(ledger_path), 0, "The ledger lists no '- R<n>: ...' requirements.")

    files = selection if selection is not None else source.features(feature_dir)
    if not files:
        report.error("no-feature-files", rel(feature_dir), 0, "No .feature files found.")

    names: dict = {}
    coverage: dict = {rid: [] for rid in requirements}
    file_requirements: dict = {}
    scenario_count = 0
    for path in files:
        relative = rel(path)
        feature = parse_feature(path, source.read(path), relative, report)
        if not feature:
            continue
        check_structure(feature, relative, report, max_steps, names, domain_terms)
        traced = file_requirements.setdefault(relative, set())
        for scenario in feature.scenarios:
            if scenario.kind == "background":
                continue
            scenario_count += 1
            tags = requirement_tags(scenario)
            if not tags:
                report.error("untraced-scenario", relative, scenario.line,
                             f"'{scenario.name}' has no @R tag. Tag the requirement it verifies, or drop it if no requirement asks for it.")
            for tag in tags:
                rid = tag[1:]
                traced.add(rid)
                if requirements and rid not in requirements:
                    report.error("unknown-requirement", relative, scenario.line,
                                 f"'{scenario.name}' is tagged {tag}, which isn't in the ledger.")
                elif rid in coverage:
                    coverage[rid].append(f"{relative}:{scenario.line} {scenario.name}")

    uncovered = [rid for rid, hits in coverage.items() if not hits]
    if selection is None:
        for rid in uncovered:
            report.error("uncovered-requirement", rel(ledger_path), requirements[rid]["line"],
                         f"{rid} ({requirements[rid]['text']}) has no scenario tagged @{rid}.")

    covered = sum(1 for hits in coverage.values() if hits)
    result = {
        "featureDir": str(feature_dir),
        "ref": getattr(source, "ref", None),
        "glossary": str(glossary) if glossary else None,
        "ledger": {"path": str(ledger_path), "requirements": list(requirements), "openQuestions": open_questions},
        "files": [rel(p) for p in files],
        "summary": {
            "files": len(files),
            "scenarios": scenario_count,
            "requirements": len(requirements),
            "covered": covered,
            "openQuestions": len(open_questions),
            "errors": report.count("error"),
            "warnings": report.count("warning"),
        },
        "coverage": coverage,
        "fileRequirements": {path: sorted(rids, key=requirement_order) for path, rids in file_requirements.items()},
        "issues": [vars(i) for i in sorted(report.issues, key=lambda i: (i.level != "error", i.file, i.line))],
    }
    if selection is not None:
        # Information, not errors: the area's other .feature files cover these.
        result["selection"] = {"files": [rel(p) for p in files],
                               "uncoveredRequirements": sorted(uncovered, key=requirement_order)}
    return result


def requirement_order(rid: str) -> tuple:
    digits = re.sub(r"\D", "", rid)
    return (int(digits) if digits else 0, rid)


def print_human(result: dict) -> None:
    for level in ("error", "warning"):
        items = [i for i in result["issues"] if i["level"] == level]
        if items:
            print(f"{level.upper()}S ({len(items)})")
            for i in items:
                location = f"{i['file']}:{i['line']}" if i["line"] else i["file"]
                print(f"  {location} [{i['code']}] {i['message']}")
            print()
    s = result["summary"]
    selection = result.get("selection")
    if selection is not None and selection["uncoveredRequirements"]:
        print(f"INFO: {len(selection['uncoveredRequirements'])} requirement(s) of the ledger are covered by none of the "
              f"selected files (the area's other files cover them): {', '.join(selection['uncoveredRequirements'])}")
        print()
    scope = "by the selected file(s)" if selection is not None else "covered"
    print(f"{s['files']} file(s), {s['scenarios']} scenario(s), {s['covered']}/{s['requirements']} requirement(s) {scope}, "
          f"{s['openQuestions']} open question(s), {s['errors']} error(s), {s['warnings']} warning(s)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("feature_dir", type=Path)
    parser.add_argument("--files", nargs="+", metavar="FILE",
                        help="check only these .feature files; requirements none of them covers are information")
    parser.add_argument("--ref", help="read from this git commit, branch or tag instead of the working tree")
    parser.add_argument("--ledger", type=Path, help="defaults to <feature-dir>/requirements.md")
    parser.add_argument("--glossary", type=Path, help="defaults to docs.glossary in .claude/atdd.json, else GLOSSARY.md")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--strict", action="store_true", help="fail on warnings too")
    args = parser.parse_args()

    source = Disk()
    if args.ref:
        source = GitRef(args.ref, Path.cwd())
        if not source.valid():
            print(f"Not a commit, branch or tag in this repo: {args.ref}", file=sys.stderr)
            return 2
    if not source.is_dir(args.feature_dir):
        print(f"Not a directory{' in ' + args.ref if args.ref else ''}: {args.feature_dir}", file=sys.stderr)
        return 2
    if args.glossary and not source.is_file(args.glossary):
        print(f"Not a file: {args.glossary}", file=sys.stderr)
        return 2
    selection = None
    if args.files:
        selection, problems = selected_files(args.feature_dir, args.files, source)
        if problems:
            print("\n".join(problems), file=sys.stderr)
            return 2
    glossary = args.glossary or find_glossary(args.feature_dir, source)
    result = run(args.feature_dir, args.ledger or args.feature_dir / "requirements.md", args.max_steps, glossary,
                 source, selection)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_human(result)
    failed = result["summary"]["errors"] or (args.strict and result["summary"]["warnings"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
