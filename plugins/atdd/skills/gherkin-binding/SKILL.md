---
name: gherkin-binding
description: How to bind approved Gherkin scenarios as honest, strict, executable acceptance tests in .NET (Reqnroll), TypeScript (Cucumber.js / playwright-bdd) or Python (pytest-bdd). Covers driving the system through its public interface, faking only external boundaries, controlling time, and the hollow-step anti-patterns reviewers reject. Use whenever writing or reviewing step definitions or acceptance-test plumbing for .feature files.
---

# Binding Gherkin honestly

A bound scenario is a promise: "this behavior works, and here's the proof". The binding must keep that promise. Four independent checks test whether it does:

- the **reviewer** reads the step definitions against the scenario text;
- **mutation testing** runs the acceptance tests and sees whether they notice broken code;
- the **QA agent** replays the scenarios by hand against the running app;
- the **quality gate** fails on undefined or pending steps.

Your stack's details are in `references/<stack>.md`, where the stack is `.claude/atdd.json` → `stack`. Read that file before writing bindings:

- `${CLAUDE_SKILL_DIR}/references/dotnet.md`: Reqnroll
- `${CLAUDE_SKILL_DIR}/references/typescript.md`: Cucumber.js / playwright-bdd
- `${CLAUDE_SKILL_DIR}/references/python.md`: pytest-bdd

## Principles

1. **Every scenario runs, strictly.**
   - The runner must fail on undefined, pending or ambiguous steps (the stack reference shows the setting).
   - Never tag a scenario `@wip`/`@ignore`, never mark it skipped or pending, never filter it out.
   - A scenario that doesn't run is a requirement silently dropped.
2. **Drive the system through its public interface.** The scenario describes the system's behavior, so the test must exercise the system the way its users do:
   - **HTTP API:** an in-process host such as WebApplicationFactory, supertest or FastAPI's TestClient, with the real routing, DI wiring, validation, serialization and persistence.
   - **Background job or message consumer:** its real entry point.
   - **UI feature:** a real browser (Playwright).

   Calling a domain service or repository directly skips exactly the layers where integration bugs live. Do that only when the scenario is really about that unit; acceptance scenarios rarely are.
3. **Fake only what's outside the system boundary:**
   - third-party HTTP APIs (WireMock.Net, msw/nock, respx/responses);
   - email/SMS gateways;
   - message brokers you don't own;
   - the clock.

   Use the real database engine. The repo's existing test-database approach (Testcontainers, a local container, SQLite only if production uses it) beats inventing a new one.
4. **Given steps arrange state the way the system would have it.**
   - Prefer the system's own interfaces for setup when they're cheap.
   - Otherwise use the repo's existing seeding helpers.
   - Store what later steps need in the per-scenario context (ScenarioContext / World / pytest fixtures). Never use static or global state: scenarios must be independent and parallel-safe.
5. **A When step performs exactly the action its sentence describes, once.**
6. **A Then step asserts exactly the outcome its sentence states, with the concrete values from the step**, observed through the public interface:
   - the response;
   - a follow-up query;
   - the message captured by the boundary fake;
   - persisted state read back the way users would see it.
7. **Control time by injecting a clock**:
   - .NET: `TimeProvider` / `FakeTimeProvider`;
   - TypeScript: a `Clock` the app already accepts, or fake timers;
   - Python: `time-machine` / `freezegun`.

   Set it from Given steps that state a date or time. No `sleep`s: poll with a timeout if something is genuinely asynchronous.
8. **Keep step definitions thin.**
   - They translate sentences into calls through a small driver (API client helper, page object), and assert.
   - Logic belongs in production code, setup belongs in fixtures, and nothing is duplicated across steps.
   - Reuse a step definition for every scenario that uses the same phrasing. Match parameters with Cucumber expressions, not catch-all regexes that ignore them.

## Hollow-step anti-patterns (reviewers treat these as blockers)

- an empty step body, or one that only logs;
- a Then with no assertion, or asserting only "not null", "status 200" or "no exception" when the step states specific values;
- a tautology: asserting what the Given step just set up;
- asserting on a mock or spy of the system under test, rather than on observable outcomes;
- calling internals (repositories, domain services, private methods) to "perform" a When, when the scenario describes a user or system action;
- a try/catch around assertions, conditional assertions, or assertions inside loops that can run zero times;
- `sleep`-based timing, or tests that depend on wall-clock time or execution order;
- stubbing the feature's own logic so the scenario passes;
- a step definition that matches a phrase but ignores its parameters.

## Done means

The checks below are what the gate verifies; do the same checks yourself before committing:

- [ ] every scenario in the feature directory runs; none is skipped, pending or undefined;
- [ ] all scenarios pass, and each failed for the right reason before the implementation existed;
- [ ] step definitions live where the stack reference says, and follow the repo's existing acceptance-test conventions if there are any;
- [ ] no file under the feature directory changed (it's locked).
