---
name: architecture-review
description: How to judge whether a change's modules, layering, dependencies and boundaries make sense in this codebase, using its ADRs, architecture docs and established patterns as the yardstick. Used by the atdd reviewer to review and by the implementer to build to the same bar. Use whenever reviewing or designing where new code belongs, how modules depend on each other, or whether a structure fits the architecture.
---

# Architecture review

The question isn't "is this good design in the abstract?" It is **"does this change fit this codebase's architecture, and will it keep the codebase easy to change?"**

The codebase's own decisions win over textbook ideals. When the code deviates from a documented decision, that's a finding. When it deviates from an undocumented but consistent pattern, ask for either alignment or a reason.

## 1. Learn the architecture before judging

Work through these sources in order of authority:

1. **ADRs and architecture docs.** `.claude/atdd.json` → `docs.architecture` names them; usually `docs/adr/` and `docs/architecture.md`. These are explicit decisions, so quote them in findings.
2. **`CLAUDE.md` / `AGENTS.md`.** Conventions the team wrote down for agents.
3. **The code's implicit architecture.** Map it quickly:
   - **Top-level structure:** projects, packages, modules and their roles.
   - **Dependency direction:**
     - .NET: `ProjectReference`s in `*.csproj`, namespaces;
     - TypeScript: imports and path aliases, plus `.dependency-cruiser.cjs` if present;
     - Python: package imports, plus `[tool.importlinter]` contracts if present.
   - **One comparable feature, traced end to end.** Find an existing feature most like this one: an endpoint, handler, job or page. Follow it from entry point to persistence. That trace is the pattern this change should follow.
4. **The glossary** (`GLOSSARY.md`). Names of modules, types and functions should use its terms.

## 2. Questions for every new or substantially changed module, type and function

**Responsibility.** Can you state what it does in one sentence without "and"? Mixed responsibilities are the most common structural defect in generated code. Typical example: a handler that validates, calculates, persists and notifies.

**Placement.** Would a newcomer look for it where it is? It should sit next to siblings that play the same role, following the traced pattern. There should be no new top-level folders or projects without a reason.

**Dependency direction.** Dependencies should only point the way the architecture allows:
- domain/core never references infrastructure, web or UI;
- features don't reach into each other's internals;
- no cycles.

A wrong-direction dependency is a **blocker**: it quietly erodes the architecture for everyone after.

**Cohesion.** Do things that change together live together? Watch for:
- a feature smeared across unrelated folders;
- new `utils`, `helpers`, `common` or `shared` dumping grounds.

**Surface area.** Is the public API as small as possible?
- C#: `internal` by default, with `InternalsVisibleTo` for tests if the repo does that;
- TypeScript: export only what callers use;
- Python: underscore-private module internals.

**Boundaries and side effects.**
- Is I/O at the edges, and is the decision logic pure enough to unit-test without infrastructure?
- Is validation at system boundaries, and nowhere else?
- Does error handling follow the codebase's convention (e.g. central middleware)?
- Are there hidden side effects (a "get" that writes)?

**Domain language.** Do names come from the glossary and the spec? Does the code model domain concepts the way the codebase already does (value types, enums, records)? If the codebase models a quantity with its own value type, raw primitives for that quantity are a finding.

**Consistency.** Does it follow the traced pattern? If it deviates, does the deviation buy something that matters? A better pattern used once is an inconsistency. Introduce it everywhere (separately), or don't introduce it.

**Abstraction level.** Watch for:
- interfaces with one implementation and no seam the tests need;
- generic frameworks for a single use;
- configuration options nobody asked for.

Three concrete uses justify an abstraction; one does not.

**Data and configuration.** Are schema changes backward compatible, and do they ship their migration? Are new settings given defaults and documented? Are there secrets in code?

**Tests at the right level.**
- Acceptance tests go through the public interface.
- Unit tests cover decision logic.
- No tests are coupled to private structure.

**Scope.** Does the diff contain only what this issue needs? Unrelated refactors, renames and config changes are findings even when they're improvements: they hide in the diff and dodge review.

## 3. What not to flag

The quality gate measures these deterministically, so leave them out unless they reveal a design problem the numbers don't show:

- formatting;
- complexity scores, coverage numbers, duplication percentages.

Also skip:

- personal taste that the codebase doesn't share;
- textbook patterns the codebase doesn't use;
- hypothetical future needs.

## 4. Severity

| Severity | Architecture examples |
|---|---|
| blocker | wrong dependency direction or cycle; violates an ADR; domain logic in infrastructure/UI (or the reverse) in a way that breaks testability; security boundary bypassed; breaking schema change without migration |
| major | mixed responsibilities in a new type; misplaced module; new dumping-ground module; needless abstraction layer; public surface far wider than needed; inconsistent with the traced pattern without reason; out-of-scope changes |
| minor | naming that misses the glossary term; small placement polish; an `internal` that could be `private` |
| nit | preferences |

## 5. Writing a finding

Give:

1. **Location:** file and line.
2. **The rule:** quote the ADR or doc, or name the pattern with an example path ("features follow `src/Features/<Feature>/*Handler.cs`").
3. **The problem:** what goes wrong if it stays.
4. **The fix:** concretely, where it should live or what it should depend on instead.

A finding without a concrete fix sends the implementer guessing, which costs a whole round.
