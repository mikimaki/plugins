---
name: gherkin-style
description: House rules for turning a product spec into Gherkin acceptance scenarios and for reviewing them. Covers the requirements ledger, R-tag traceability, declarative business-language steps, boundary examples, open questions instead of invented behavior, and a deterministic checker. Use whenever you write, revise or review Gherkin or .feature files, or turn a spec, PRD or issue into acceptance criteria, BDD scenarios or acceptance tests, even if Gherkin isn't named.
---

# Gherkin style

The Gherkin produced here is an **executable specification** with two readers:

- **Product people** read it to confirm "yes, that's the feature".
- **Step definitions** turn each scenario into an automated acceptance test.

Every rule below serves one of those two readers. Downstream, an implementer makes these scenarios pass and a QA agent replays them against the running app. A missing, invented or vague scenario becomes missing, invented or untested behavior.

## The files for one issue

Everything for one issue lives in `<featuresDir>/<issue-key>/`:

```
<featuresDir>/<issue-key>/
├── requirements.md        # the requirements ledger (below)
└── <capability>.feature   # one file per cohesive capability; usually 1–3 files
```

`featuresDir` comes from the repo's `.claude/atdd.json`.

## 1. Start with the requirements ledger

Before you write a single scenario, list the spec's testable behaviors in `requirements.md`:

```markdown
# Requirements: PROJ-45 <issue title>

Source: <the issue link, or the tracked spec file path>

- R1: <one observable behavior, in the spec's and the glossary's terms>
  > Spec: "<the spec sentence it comes from, quoted exactly>"
- R2: <the next behavior>
  > Spec: "<its source sentence>"

## Out of scope
- <what the spec explicitly leaves out, and where it's handled if the spec says>

## Open questions
- None
```

What makes a good entry:

- **One observable behavior per R-id.** It must be something a test can check. "Responds quickly" is not a requirement; "responds within 2 seconds" is, when the spec says 2 seconds.
- **A quote of the spec sentence it comes from.** This is what makes the ledger checkable.
- **Plain spec language**, not implementation detail.
- **Rules, outcomes, constraints and stated error behavior all count.** Implementation choices don't.

**Why the ledger comes first.** It is the contract between the spec and the scenarios:

- Coverage becomes mechanical: every R-id needs a scenario.
- Invention becomes visible: every scenario must carry an R-tag, so a scenario with nothing to point at stands out.

**Format the checker relies on.** Write each requirement as a bullet: `- R<n>: <behavior>`. Under `## Open questions`, write one top-level bullet per question, or `- None`.

**The `Source:` line** points at where the spec lives for everyone: the issue link, or the spec's tracked path. Never point it at the `.atdd/` snapshot. That copy is git-ignored, so the link would dangle for every other reader.

## 2. Don't invent behavior

When the spec doesn't say what happens, that's an **open question**, not a scenario. Typical gaps:

- an error case the spec doesn't mention
- an undefined limit
- contradictory sentences

Record each one under `## Open questions` with:

- what's unclear, quoting the spec;
- why it matters;
- the options you see.

Then leave that behavior unscenarioed.

You may also draw on:

- the repo's glossary (`GLOSSARY.md`);
- architecture docs and ADRs that state existing system-wide rules, such as "all endpoints require an authenticated user".

Cite the source in the ledger entry. Anything else you "know" about how features like this usually work is a guess. A guess turned into a scenario becomes a requirement nobody asked for.

## 3. Write declarative steps in business language

Describe **what** the actor achieves and **what** the system guarantees. Leave out **how** they operate the UI or the API. Step definitions hide the mechanics, so the scenario survives a UI redesign or a new transport.

The examples below use generic software features (uploads, exports, display names) only to show the shape. Your steps use the vocabulary of your spec and glossary.

| Instead of (imperative / technical) | Write (declarative) |
|---|---|
| `When I click "Export" and choose "PDF"` | `When the user exports the report as a PDF` |
| `Given I POST /files with {"name": "q3.csv"}` | `Given the user has uploaded the file "q3.csv"` |
| `Then the response status is 413` | `Then the upload is rejected because the file is larger than 10 MB` |
| `Then the export works as expected` | `Then the PDF lists all 4 rows of the report` |

Keep these rules in mind:

- **Use the glossary's terms exactly.** If the spec uses a term the glossary lacks, keep the spec's term and mention it in your summary. Synonyms (two words for the same role or thing) make two readers think of two different things.
- **Protocol and UI details are allowed only when the spec makes them part of the contract.** One example is a public API spec that defines its error codes.
- **Name the actor with the glossary's term for that role.** Fall back to a generic "the user" only when the glossary has no better term. Use "I" only if the repo's existing features do.

### When the spec describes a screen

Product specs often describe the UI:

> taps "Edit profile", types a name into the "Display name" field and taps "Save" … the form shows "Enter a display name" under the field

Translate it. Don't transcribe it:

- **Interactions become intentions.** Taps, clicks, typing and selecting become what the actor achieves: `When the user changes their display name to "Robin"`. Softer verbs are interactions too: "opens", "goes to", "enters", "fills in" and "picks" still describe the screen. Say what the actor is opening or entering something *for*.
- **UI element names become glossary terms.** Cards, fields, buttons, banners, pages, screens and captions are implementation. Name the thing a field holds by the glossary's term for it, not by the field's label and not by the act of filling it in: `without a display name`, not `without entering a name`.
- **User-visible text the spec defines stays verbatim, in quotes, as the outcome:** `Then the user is told "Enter a display name"`, `Then the user sees "Saved"`. Quoted text is exempt from the UI-word check.
- **Placement ("under the field", "on the card") and widget state ("the field is pre-filled with Robin") are layout.** State the behavior instead: `Then the user is offered their current display name "Robin"`. Keep placement only when the spec makes it the point of the feature.

| Transcribed from the screen (avoid) | Translated (write) |
|---|---|
| `When the user taps "Save" with the field empty`, or the subtler `When the user saves without entering a name` | `When the user saves their profile without a display name` |
| `When the user opens the profile form` | `When the user starts changing their display name` |
| `Then the form shows "Enter a display name" under the field` | `Then the user is told "Enter a display name"` |
| `Then the display name field is pre-filled with "Robin"` | `Then the user is offered their current display name "Robin"` |

A UI word that is part of a domain term, such as a glossary entry that happens to contain "card" or "page", is not UI coupling. The checker reads the repo's glossary and doesn't flag those.

## 4. One behavior per scenario

- **Given:** the context, as state that already holds.
- **When:** one trigger, either an action or an event.
- **Then:** the observable outcome(s) of that trigger. `And`/`But` continue whichever block they're in.
- **The title states the behavior and its outcome**, for example "A file of exactly 10 MB is accepted". If you need "and" in a title, you probably have two scenarios.
- **No journeys.** If a scenario goes back to When after a Then, it's testing two behaviors: split it.
- **Keep scenarios short: 8 steps or fewer.**
- **Use a `Background:` only for context every scenario in the file needs.** Keep it to 4 Given steps or fewer.
- **Group scenarios with `Rule:`** when a capability has several business rules. A rule-level `@R` tag covers every scenario in the rule.

## 5. Concrete examples, explicit boundaries

- **Use realistic values** from the spec's world: real-looking names, amounts with their units, calendar dates ("10 MB", "2026-03-08", "a user named Robin"). Never "X", "foo" or "some value". Concrete values are what make a scenario unambiguous to implement.
- **When a rule has a threshold, limit, date or count, cover just below, at, and just above it.** Use a `Scenario Outline` with an `Examples` table whose name says what varies. Off-by-one is the bug class acceptance tests most often miss.
- **Cover the rejection or error behavior the spec defines** for invalid input. Don't make up error behavior it doesn't define (see §2).
- **Make time explicit when behavior depends on it**, including the time zone: `Given it is 2026-03-08 01:30 in America/New_York`. That example sits 30 minutes before a DST change. Tests need a controllable clock, and day boundaries and DST are where these features break. If any rule turns on *when* something happens (before or after a deadline or cut-off), pin the clock in every scenario touching that rule, not just the one that tests the cut-off itself. Otherwise "now" defaults to whenever the test runs, and the scenario means something different every day.
- **Check behavior, not only stored state.** When the actor can switch something off and back on, cover the behavior in each state: while it's off, the rule doesn't apply; once it's back on, the rule applies again with the kept settings. A setting that is still stored proves nothing about the behavior it controls.
- **Givens are state, not actions.** Write `Given two-step sign-in is on for the user`, not `Given the user turns on two-step sign-in`. Don't let a scenario's Givens contradict its Background. If they would, the Background holds context that not every scenario shares, so move it into the scenarios that need it.
- **Use data tables** for structured inputs and outputs. If a table grows beyond a handful of rows, the scenario is probably testing too much.

## 6. Tags

| Tag | Where | Purpose |
|---|---|---|
| `@issue-<key>` | Feature line | Ties the file to the issue |
| `@R<n>` | Scenario, Scenario Outline, Examples or Rule | Traceability: names the requirement(s) this scenario verifies. Every scenario needs at least one |

- **Don't put `@R` tags on the Feature line.** That would claim every scenario covers every requirement, which hides real coverage.
- **Don't invent other tags.** Use only tags that the repo's existing features or `.claude/atdd.json` already establish.

## 7. Consistency with the repo

Read existing `.feature` files before writing new ones and match them: actor naming, tense, phrasing of common steps (step reuse keeps step definitions small), and how domain objects are introduced. Do not change existing feature files that belong to other issues.

## 8. Run the checker

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/gherkin_check.py <featuresDir>/<issue-key>
```

**Errors** are deterministic failures. Fix all of them:

- an uncovered or unknown requirement
- an untraced scenario
- broken Given→When→Then order
- Outline/Examples mismatches
- a missing Then
- parse errors

**Warnings** are heuristics: imperative or technical vocabulary, vague outcomes, long scenarios, several actions in one scenario. Treat each as a prompt to reconsider. Keep the step if the spec justifies it.

The checker reads the glossary that `.claude/atdd.json` names (or `GLOSSARY.md` at the repo root), so a UI word inside a glossary term isn't flagged. Pass `--glossary <path>` to point it elsewhere.

Add `--json` for machine-readable output.

## 9. Review checklist and severities

Use this when reviewing Gherkin (yours or someone else's). Only **blocker** and **major** findings send work back. Everything else is advice.

**Blocker** — the specification is wrong:
- A requirement from the spec is missing from the ledger or misread.
- A scenario asserts behavior the spec doesn't state (invented).
- A scenario contradicts the spec.
- A Then step can't be verified.
- The checker reports errors.

**Major** — the specification is weak:
- A boundary, limit or error case **that the spec defines** has no scenario.
- A step is worded so loosely that two implementers would build different things.
- A step is imperative or UI/transport-coupled where a declarative one fits. This includes naming UI elements (field, card, button, banner, page, screen) or widget state outside quoted user-visible text, unless the word is part of a glossary term. Softer interaction verbs (opens, enters, fills in, picks) count too.
- A switch (off, on, back on) is verified only by the stored setting, not by the behavior it controls.
- A scenario covers more than one behavior.
- Domain terms are inconsistent or missing from the glossary.
- An open question is really answerable from the spec, or a genuine gap is papered over with a guess.

**Minor:**
- clumsy titles
- repetition an Outline would remove
- step phrasing that doesn't reuse existing steps

**Nit:**
- wording preferences

**Calibrate.** A reviewer asked to find problems will always find some. Report a finding as blocker or major only if a product owner would care, or an implementer would go wrong.
