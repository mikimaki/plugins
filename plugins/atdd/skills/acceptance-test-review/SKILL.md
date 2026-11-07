---
name: acceptance-test-review
description: How to judge acceptance tests that cover behavior a codebase already has, in a change that may add tests and nothing else (what /atdd:cover produces). Checks that no file outside the test paths changed, that step definitions honestly bind the approved scenarios to the real system, that the tests make a trustworthy safety net for later refactoring (behavior-level, deterministic, failing when behavior breaks), the coverage targets, and the test code, with a severity scale that never asks for production changes. Used by the atdd coverage reviewer, and by the acceptance-test-coverer to know the bar. Use whenever reviewing tests added to existing or legacy code, characterization tests, or a branch that should only add tests.
---

# Reviewing acceptance tests for existing code

The change under review adds acceptance tests to code that already exists, and it may not change anything else. Its value depends on the code staying exactly as it was: these tests will be the safety net for later work that refactors and re-architects that code. Judge the tests by two questions:

1. **Did anything besides tests change?** If so, the work may have changed what the platform does.
2. **Can later work trust these tests?** They must stay green through a refactoring that keeps the behavior, and turn red when the behavior breaks.

**The production code is out of scope.** Don't review it, and don't suggest changing it, not even as a nit. Its structure, names, missing validation and quirks are what later work will improve, once these tests are in place. A review that asks for production changes pulls the work into exactly what it must not do.

## 1. Nothing outside the test paths changed

Check this first, over the whole branch, with the gate's deterministic check:

```bash
ATDD_BASE=<base> ATDD_OUT=.atdd/<issue-key>/cover/review python3 scripts/atdd/gate.py cover-fast --only production-untouched
```

It lists every file that differs from the base and lies outside the test paths, committed or not. The test paths are `cover.testPaths` in `.claude/atdd.json`, or else every `gate.testDirs` folder plus `featuresDir`. A modified, added, deleted or renamed file all count.

Any entry is a **blocker**, whatever the change: a whitespace fix, a comment, a renamed variable, a configuration value, a package bump, a lockfile, a migration, a doc. Don't weigh whether it's harmless. The rule exists so that nobody has to.

Then read the diff inside the test paths for changes that alter behavior from there:

- test support that patches production modules for every test, such as an autouse fixture in a root `conftest.py` or a setup file that swaps one of the app's own modules;
- test configuration that the real app would read;
- existing tests changed, weakened or deleted. The safety net that was there before must stay intact. Extending shared acceptance-test support, such as a driver or a fixture, is fine when the tests that use it pass unchanged.

## 2. Each step definition keeps its scenario's promise

Read every scenario next to the step definitions it runs. The hollow-step anti-patterns in `gherkin-binding` are **blockers**. Existing code makes four more of them tempting:

- **Characterization drift.** The Then asserts what the code happens to return instead of what the step states. The scenario says the guest is told "Choose a check-out date after your check-in date"; the step checks for a 422, or for any non-empty message. When the code returns something other than the scenario says, the coverer should have reported a discrepancy, not softened the assertion.
- **Arranging the impossible.** A Given inserts rows the real system couldn't produce, or skips the state transitions users go through. The scenario then proves behavior for data that can't exist.
- **Faking the system under test.** The test mocks or patches the platform's own services, repositories or handlers, even "just to make the setup easier". Only boundaries the platform doesn't own may be faked: third-party APIs, email and SMS gateways, external brokers, the clock.
- **Test-only behavior.** The test host has configuration the real app doesn't: auth switched off, a feature flag forced on, a different storage engine. That's fine only when the scenario itself states the condition.

A scenario that can't be bound honestly without changing production code calls for a missing-seam report from the coverer (`blocked`), not a workaround.

## 3. A safety net for refactoring

Ask of every step: if later work restructured the code but kept the behavior, would this test survive? And if later work broke the behavior, would this test notice?

**It survives restructuring:**

- It drives the system through its public interface: HTTP, the UI, or the entry point a client uses. Not internal classes, private methods or reflection.
- It observes outcomes the way a user or client would: the response, a follow-up query through the interface, the message a boundary fake captured. Reading the database directly is a last resort, for state no interface exposes.
- It asserts what the Then states and nothing incidental. That rules out the full response body when the Then names one field, and log lines, generated ids, exact SQL, internal class names, or an ordering the scenario doesn't state. Snapshots of whole payloads lock in structure.
- Its setup goes through the system's interfaces or the repo's seeding helpers, not hand-built internal objects.

**It fails when behavior breaks:**

- Every concrete value in the Then is asserted exactly.
- Negative outcomes are asserted positively. "Is refused" checks the refusal itself and that nothing was stored, not merely that nothing threw.
- It's deterministic:
  - time comes from a controlled clock;
  - each scenario creates its own data;
  - there's no `sleep`, and no dependence on execution order, a real third-party service, the wall clock, or data left in a shared database.

## 4. Coverage targets

The coverage targets file, `.atdd/<issue-key>/cover/targets.txt`, lists the production files whose behavior the scenarios pin down. The quality gate measures the tests on exactly these files. Check that:

- every entry is production code, not a test or a generated file;
- the files implementing the ledger's behaviors are all there. Trace one scenario's When to the code that handles it, and check that every file deciding an outcome on the way is listed;
- it isn't padded with files the scenarios merely pass through, such as framework setup, generic middleware or the ORM context. Padding buries real gaps in noise.

A wrong list is a **major**: it makes the gate measure the wrong thing.

## 5. The test code

Test code is code, and `coding-standards` applies to it:

- step definitions stay thin and use the scenario's language; interaction details live in drivers or page objects;
- there's one step definition per phrasing, with parameters matched by Cucumber expressions;
- per-scenario state lives in the runner's context, never in module-level or static variables;
- there are no narration comments, dead helpers or copy-pasted setup;
- names come from the glossary.

## Severity

Only blockers and majors send the work back.

- **Blocker:**
  - any file outside the test paths changed;
  - a hollow or lying step definition: a `gherkin-binding` anti-pattern, characterization drift, or a faked system under test;
  - a scenario that doesn't run, or is skipped, tagged away or pending;
  - existing tests deleted, weakened or rewritten;
  - test code that changes how other tests or the app behave, through global patches or shared configuration;
  - a modified locked file.
- **Major:**
  - an assertion weaker than its Then, or one that would pass with the outcome broken;
  - coupling to structure: internals, reflection, internal tables, incidental output, whole-payload snapshots;
  - nondeterminism: wall-clock time, sleeps, order dependence, shared data, a real external service;
  - test-only behavior that the scenario doesn't state;
  - a wrong coverage targets list;
  - duplication or logic in step definitions that will make the suite hard to maintain.
- **Minor / nit:** naming, structure polish, readability preferences.

**Never a finding:** anything about the production code itself, such as its design, names, complexity, error handling or bugs. If the code visibly contradicts a scenario while its test passes, the finding is the lying test.

Give every finding:

- the file and line;
- the rule it breaks, quoted from this skill, `gherkin-binding`, the scenario or the spec;
- the fix, in the tests.

Mark a finding for a human only when no change to the tests can resolve it, for example a scenario that needs a production seam the code doesn't have.
