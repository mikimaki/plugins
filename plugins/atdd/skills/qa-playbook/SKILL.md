---
name: qa-playbook
description: Manual QA method for the atdd qa agent. Start the app from the repo's ATDD config, replay every Gherkin scenario by hand through real HTTP calls or a real browser, explore around the change with proven heuristics (including Next.js specifics such as server actions, hydration errors and stale data), and file reproducible findings with evidence (Hurl files, curl scripts, screenshots). Use whenever manually testing a feature against a running application.
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
2. **Poll the health URL in the background** for up to about 2 minutes before declaring the app broken, or about 6 minutes when `qa-up` builds the app first (`qa.notes` says so; a Next.js app does). Start the polling loop with `run_in_background` (for example `until curl -sf "$HEALTH_URL" >/dev/null; do sleep 2; done; echo ready`) and monitor its output until it prints `ready` or the time is up. Never poll in a foreground loop: a guard refuses foreground shell loops with `sleep`, single `sleep`s over 30 seconds and foreground timeouts over 2 minutes. If the app doesn't come up, that's a **blocker**. Report it with the last 50 lines of the app log (`qa-up` writes it to `.atdd/qa/app.log` unless `qa.notes` says otherwise).
3. **Always run `commands.qaDown` at the end**, also after failures. Stop anything else you started.

## 2. Replay every scenario by hand

For each scenario in the feature directory:

1. **Arrange the Given state the way a user or client would:** through the app's APIs or UI, or with the seeding approach in `qa.notes`. Don't use the test suite's helpers. Bypassing them is the point.
2. **Perform the When**, and observe the Then with your own requests or eyes.
3. **Record the result** in `<evidence-dir>/scenarios.md`: one line per scenario with pass, fail or skipped (with the reason) and a pointer to the evidence.
4. **Save its replay script** in `.atdd/<key>/qa/scenarios/`, whether it passed or failed, overwriting last round's. A scenario skipped for an idle wait (see "Idle waits" below) gets none.

**Replay scripts.** Every QA run leaves one replay script per scenario in `.atdd/<key>/qa/scenarios/`, so a later round replays all of them with the one command below:

- **API scenarios:** `<scenario>.hurl`, taking the base URL from the `base` variable.
- **UI scenarios:** `<scenario>.mjs`, a standalone Node script using the `playwright` library (`import { chromium } from 'playwright'`). It reads the base URL from `process.env.BASE`, launches a headless browser, and exits non-zero when a check fails (an uncaught error or a failed `assert` does that).
- **Self-contained:** each script sets up its own Given state through the app's real interfaces, never relying on what an earlier script or round left behind. Generate unique values (a timestamp or random suffix) where the app forbids duplicates.
- **No script possible** (no `hurl` installed for an API scenario, no `playwright` package for a UI one): the scenario has no replay script, and a later round hand-tests it. Write its steps in `<evidence-dir>/scenarios.md` instead; a step list isn't a replay script and never goes in the scenarios folder.

Evidence for findings, including curl scripts, stays in `round-<n>/`; the scenarios folder holds only replay scripts.

The example replays a generic "an empty display name is refused" scenario. Your files follow your scenarios and your API.

```hurl
# .atdd/proj-45/qa/scenarios/empty-display-name-refused.hurl
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

**Replay command.** One invocation runs every script in the folder, API and UI, and fails if any of them fails:

```bash
BASE=<qa.baseUrl> sh -c 'cd .atdd/<key>/qa/scenarios || exit 1; rc=0
for f in *.hurl; do [ -e "$f" ] && { hurl --test --variable base="$BASE" "$f" || rc=1; }; done
for f in *.mjs; do [ -e "$f" ] && { node "$f" || rc=1; }; done
exit $rc'
```

A scenario whose script fails shows in the output by file name.

**For UI** (when `qa.ui` is true), use the Playwright MCP browser tools when they're available. Find them with ToolSearch under the `playwright` prefix.

- Navigate.
- Read the page through accessibility snapshots, not guesses.
- Click and type like a user.
- Take a screenshot for every failure and every scenario's end state.

Without the MCP tools, run the scenario's `.mjs` replay script with `node` directly. Either way, save the scenario's replay script in the scenarios folder.

**Time-dependent scenarios:** use the app's test clock if `qa.notes` names one. With a test clock, fast-forward idle waits instead of skipping.

**Idle waits.** An idle wait is time you spend after acting in a scenario during which the outcome depends only on a scheduled trigger in the app firing (a cron job, timer, delay, expiry or poll interval), not on work in progress.

- **Idle** (skip if over 2 minutes): a nightly cron job; a queued job picked up by a worker that polls every 5 minutes; a 15-minute lockout or rate-limit cooldown; a token or record expiry.
- **Active** (never skip): a page that takes 3 minutes to load; a report that takes 10 minutes to compute; a worker that picks a job up immediately and processes it for 5 minutes; an email or webhook through a provider with unknown delivery delay.

The rule:

1. **A single idle wait over 2 minutes means skip and report the scenario.** The limit applies to each idle wait on its own, not to the scenario as a whole: idle waits of 2 minutes or less are waited out in the background and verified normally, even if a scenario has several.
2. **Slow is never a reason to skip.** Slow pages, long calculations, slow builds, slow startup and any other long active work run in the background (`run_in_background`), and you monitor them until they finish.
3. **Find the trigger's schedule** in the code and config. If you can't, wait up to 2 minutes in the background. If the outcome hasn't appeared by then, skip with a reason saying the length is unknown, e.g. `idle wait: report poll, length unknown, nothing appeared within 2 minutes`.
4. **The reason names the trigger and its length when known**, e.g. `idle wait: nightly cron at 02:00 (~11h)` or `idle wait: 15-minute lockout expiry`.
5. **Record the skip** with its reason in `<evidence-dir>/scenarios.md` and in your result's `skipped` list. A skip is not a finding and doesn't fail your verdict. A skipped scenario has no replay script.

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

**Next.js apps** (`stack` is `nextjs`). The app runs as a production build. Besides the heuristics above:

- **Read the browser console** on every page you test (`browser_console_messages` in the Playwright MCP tools). A hydration mismatch, an uncaught error or a failed request is a finding even when the page looks right. A production build minifies React's messages ("Minified React error #418"); quote them with the link they print.
- **Server actions are public POST endpoints.** Client-side checks (`required`, `min`, `max`, `pattern`, disabled buttons) are only a convenience. Remove them in the browser (`browser_evaluate`: `form.noValidate = true`, or remove the attribute) and submit values the server must refuse: empty, out of range, another user's id in a hidden field. A refusal must come from the server, with the spec's message.
- **Route handlers** (`/api/...`) are tested like any HTTP API, with Hurl.
- **Fresh data after a change:** after a save, the page, a reload, a second tab and client-side navigation back to it all show the new state. Stale data after a server action usually means a missing `revalidatePath` or `revalidateTag`.
- **Navigation:** open deep links directly, use back and forward, and reload mid-flow. An unknown id shows the app's not-found page, not an error page; a failing request shows the route's error state, not a blank screen.
- **Persistence:** check stored state through the app. If the UI can't show it, a read-only query against the development database is acceptable evidence; say so in the finding.

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
- **Blocker:** a scenario fails against the real app by hand; a scenario that passed before fails on replay; data loss; a security hole; the app doesn't start.
- **Major:** spec-defined behavior is wrong outside the scenarios' exact examples; a swallowed or meaningless error; a reachable unhandled exception.
- **Minor:** cosmetic issues, wording, pre-existing issues.

## 5. Later rounds

When the prompt names the commit you last tested, sections 2 and 3 shrink to three steps:

1. **Re-test every earlier finding by hand** first, rerunning its evidence.
2. **Replay all saved scripts** with the replay command in section 2. Fixes often break neighbors. Hand-test only the scenarios that have no script, following the steps in last round's `scenarios.md`. Save fresh replay scripts as in section 2.

   A scenario skipped earlier for an idle wait also has no script, but don't hand-test it: that would need the same wait. It stays skipped. Record it again in `scenarios.md` and in `skipped` with the same reason.
3. **Explore only around** `git diff <last tested>..<commit under test>` and what it affects, with section 3's heuristics.

- A replay failure of a scenario that passed before is a **blocker**. Attach the replay output as evidence.
- A script that can't run again because of leftover state (a duplicate unique value, a record from last round) is not a finding. Fix the script so it sets up its own state, and hand-test that scenario this round.
