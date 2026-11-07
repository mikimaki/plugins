# Binding scenarios to code you may not change

`/atdd:cover` binds approved scenarios to behavior the code already has. Everything in `SKILL.md` still holds: the public interface, the real database, fakes only at the boundary, controlled time. One constraint changes how you get there: **production code, configuration and build files stay exactly as they are.** Every seam a test needs has to come from outside the code:

- the test host;
- configuration the app already reads;
- interception at the network level;
- the runtime.

## No red phase

ATDD normally proves a test can fail by running it before the code exists. Here the code exists, so a correct binding passes at once, and a passing test proves nothing yet. Two things take the red phase's place:

- **You**, for each Then that could be hollow: break its expectation in the step definition for a moment, watch the scenario fail with a message that names the outcome, then restore it.
- **The quality gate**, whose acceptance mutation testing breaks the production code on purpose and checks whether the scenarios notice.

## Seams that need no production change

| Need | .NET | TypeScript / Node | Next.js | Python |
|---|---|---|---|---|
| Host the app for tests | `WebApplicationFactory<T>`. `T` can be any public type in the web project, so a `Program` the tests can't see is no obstacle | the app's exported composition root (`buildApp`, `createServer`); without one, start the real server process on a test port | the production build, which the templates' Playwright config builds and starts | the app factory, or the module-level `app`, with `TestClient` or `httpx.ASGITransport`; Django's test client |
| Replace a registered service at the boundary | `ConfigureTestServices`: `RemoveAll<T>()`, then add the fake | the composition root's options, when it takes them | none in the running server: use configuration the app already reads | FastAPI's `app.dependency_overrides`; Django's `override_settings` |
| Fake a third-party HTTP API | point the base URL the app reads from configuration at WireMock.Net (`UseSetting`, in-memory configuration), or replace the primary handler of its named or typed `HttpClient` | `msw` or `nock`, in-process; for a separate server process, point the base URL it reads from the environment at a local stub | a local stub server, with its URL in `webServer.env` | `respx` for httpx, `responses` for requests |
| Control time | replace `TimeProvider` with `FakeTimeProvider` when the app resolves it from DI | `@sinonjs/fake-timers` with `toFake: ['Date']` (faking the timers too would stall the server) | the browser: `page.clock`. The server: only through a variable the app already reads | `time-machine` or `freezegun` |
| Database | the connection string from configuration, pointed at Testcontainers or a test database | the URL the app reads from the environment | `TEST_DATABASE_URL`, as the templates set up | the setting or variable the app reads, and the repo's test-database fixture |

Patching the app's own modules is faking the system under test, even when the goal is to reach a boundary: `mock.patch("app.services…")`, `vi.mock('../src/…')`, or replacing an internal service in DI. Intercept at the network level instead.

When a third-party SDK talks to its service in a way the network fakes can't intercept, fake the SDK's own client class and say so in your summary. Don't fake the app's adapter around it: the adapter is part of the system under test.

## When there's no seam

Sometimes there's no way in without changing the code:

- the code reads the clock directly (`DateTime.UtcNow`, `datetime.now()` in a process you can't patch), and a scenario depends on the date;
- the code builds an HTTP client inline, with a hard-coded URL, in a process you can't intercept;
- a static or global holds state you can't reset between scenarios.

Then stop and report `blocked`. Give the file and line, what the scenario needs to control, and the smallest seam a human would add, such as injecting `TimeProvider` or reading the URL from configuration. Faking around a missing seam would bind the scenario to something other than the real system. A human adds the seam in a change of its own, on the base branch, and covering resumes after that.

## Data in an existing system

- Arrange Given state through the system's interfaces where they exist. Otherwise use the repo's seeding helpers or factories. Never insert rows the system couldn't have produced.
- Each scenario creates its own data, with unique names or ids, so scenarios stay independent and safe to run in parallel.
- Never run against a database people use. The test database comes from configuration the app already reads.

## When the code disagrees with a scenario

The scenarios came from a spec someone wrote about the platform, so some may describe what someone believes it does rather than what it does. When a scenario fails and both your binding and your test setup are right:

- don't change the code;
- don't bend the step to what the code does: no softer assertion, different value or broader match;
- don't skip, tag or delete the scenario.

Record a discrepancy instead:

- the scenario and the failing step;
- what the step expects;
- what the code does instead, observed and quoted;
- where the code decides it (`file:line`).

A human decides which side is wrong. Either the scenario gets fixed, or the bug does, in a change of its own.

## Don't pin what the scenario doesn't say

Existing code produces plenty of incidental output: full payloads, generated ids, timestamps, log lines, orderings. Assert what the Then states and nothing more. Pinning incidental output turns the safety net into a straitjacket that every harmless refactoring breaks.
