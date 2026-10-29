---
name: qa-playbook
description: Manual QA method for the atdd qa agent. Start the app from the repo's ATDD config, replay every Gherkin scenario by hand through real HTTP calls or a real browser, explore around the change with proven heuristics, and file reproducible findings with evidence (Hurl files, curl scripts, screenshots). Use whenever manually testing a feature against a running application.
---

# QA playbook

Manual QA exists because green automated tests can still hide a broken product. Your job is to use the running system the way its users and clients will, and to report every gap so precisely that the implementer reproduces it on the first try.

## 1. Environment

Read `.claude/atdd.json`:

| Key | What it gives you |
|---|---|
| `commands.qaUp` | starts the app and its dependencies (database, queues), usually in the background |
| `commands.qaDown` | stops them |
| `qa.baseUrl` | where the app listens |
| `qa.healthUrl` | what to poll until the app is ready; defaults to `qa.baseUrl` |
| `qa.ui` | `true` when there's a UI to test in a browser |
| `qa.notes` | test accounts, seed data, feature flags, anything else a tester needs to know |

Procedure:

1. **Start the app** with `commands.qaUp`. It should return once the app is starting. Don't run a foreground server in your own shell: it would block you.
2. **Poll the health URL** for up to about 2 minutes before declaring the app broken. If it doesn't come up, that's a **blocker**. Report it with the last 50 lines of the app log (`qa-up` writes it to `.atdd/qa/app.log` unless `qa.notes` says otherwise).
3. **Always run `commands.qaDown` at the end**, also after failures. Stop anything else you started.

## 2. Replay every scenario by hand

For each scenario in the feature directory:

1. **Arrange the Given state the way a user or client would:** through the app's APIs or UI, or with the seeding approach in `qa.notes`. Don't use the test suite's helpers. Bypassing them is the point.
2. **Perform the When**, and observe the Then with your own requests or eyes.
3. **Record the result** in `<evidence-dir>/scenarios.md`: one line per scenario with pass or fail and a pointer to the evidence.

**For APIs, write each scenario as a Hurl file.** The file is executable, reproducible evidence. If `hurl` isn't installed, use a small curl script with `set -e` and explicit checks instead.

The example replays a generic "an empty display name is refused" scenario. Your files follow your scenarios and your API.

```hurl
# .atdd/proj-45/qa/round-1/empty-display-name-refused.hurl
POST {{base}}/users
Content-Type: application/json
{ "displayName": "Robin" }
HTTP 201
[Captures]
user_id: jsonpath "$.id"

PATCH {{base}}/users/{{user_id}}
Content-Type: application/json
{ "displayName": "" }
HTTP 422
[Asserts]
jsonpath "$.message" == "Enter a display name"

GET {{base}}/users/{{user_id}}
HTTP 200
[Asserts]
jsonpath "$.displayName" == "Robin"
```

```bash
hurl --test --variable base=<qa.baseUrl> .atdd/proj-45/qa/round-1/*.hurl
```

**For UI** (when `qa.ui` is true), use the Playwright MCP browser tools when they're available. Find them with ToolSearch under the `playwright` prefix.

- Navigate.
- Read the page through accessibility snapshots, not guesses.
- Click and type like a user.
- Take a screenshot for every failure and every scenario's end state.

Without the MCP tools, a short Playwright script via `npx playwright` works too. Save it as evidence.

**Time-dependent scenarios:** use the app's test clock if `qa.notes` names one. Otherwise test what you can with data timestamps, and mark the scenario `not manually verifiable: needs a controllable clock` (a minor finding).

## 3. Explore around the change

Spend real effort here; this is where QA earns its keep. Stay within the issue's area.

**Boundaries.**
- Every limit in the ledger: just below, at, and just above.
- Zero, negative, maximum and enormous values.
- Empty collections.

**Invalid input.**
- Missing or extra fields, wrong types, nulls.
- Empty strings and whitespace.
- Unicode, emoji and long strings.
- Malformed JSON and the wrong content type.

**Auth and tenancy.**
- No credentials, expired credentials.
- Another user's or tenant's resource (IDOR).
- A role without permission.

**Repetition.**
- Double submit, retried requests, idempotency keys if the API has them.
- The same event twice.

**Concurrency.** Parallel requests that touch the same resource, for example ten identical requests at once with `xargs -P`.

**Persistence.** Restart the app (`qaDown` then `qaUp`) and check that state survived.

**Time.**
- Day, month and year boundaries.
- DST changes.
- The user's timezone vs UTC.

**Errors.**
- Is every error message actionable?
- Does any error leak stack traces, SQL or internal ids?
- Are failures logged?

**UI.**
- Empty, loading and error states.
- Keyboard-only use, visible focus, form labels.
- Narrow viewport.
- Behavior after a page refresh.

Pre-existing bugs outside the issue's scope go in as **minor** notes. They aren't this change's fault.

## 4. Findings

Each finding must stand alone:

```text
id: qa-3
severity: blocker | major | minor | nit
title: Two parallel saves create two records
scenario/area: <feature file> "<scenario title>" (or "exploratory: concurrency")
steps: hurl --test --variable base=http://localhost:5080 .atdd/proj-45/qa/round-1/qa-3.hurl
expected: <the rule, quoted from the spec, with its @R tag>
actual: two records, created 3 ms apart (response saved in qa-3.response.json)
evidence: .atdd/proj-45/qa/round-1/qa-3.hurl, qa-3.response.json, app.log lines 120–131
```

**Severity:**
- **Blocker:** a scenario fails against the real app; data loss; a security hole; the app doesn't start.
- **Major:** spec-defined behavior is wrong outside the scenarios' exact examples; a swallowed or meaningless error; a reachable unhandled exception.
- **Minor:** cosmetic issues, wording, pre-existing issues.

**In re-test rounds:**
1. Rerun the evidence for each earlier finding first.
2. Then replay all scenarios. Fixes often break neighbors.
