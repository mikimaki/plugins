# /atdd:build speed-up: decisions

Grilling session on the handoff plan (items 1–7), 2026-11-13. Terms as in `GLOSSARY.md`.

## Settled

| # | Item | Decision |
|---|---|---|
| D1 | 1 | `build.js` stage order is review → quality → QA. Reviewer stays first (cheapest, and its refactors would make a gate result stale). `meta.phases` follows the new order. |
| D2 | 2 | QA is removed from `/atdd:cover` (and so from `/atdd:cover-queue`). See ADR-0001. Cover runs coverage reviewer → cover gate only. |
| D3 | 2 | Production code = every changed path outside the test paths (`cover.testPaths`, else `gate.testDirs` + `featuresDir`) and `.atdd/`. Same definition as the cover gate's `production-untouched`. |
| D4 | 2 | ~~The quality stage reports `productionChanged` (+ paths) against `memory.qa.lastApproved`, using `production-untouched` with that base. The workflow skips QA when it's false and logs why.~~ Superseded by D19 (mikimaki/plugins#12). |
| D5 | 2 | ~~A QA re-run: re-test earlier findings → replay every scenario from saved executable evidence (hand-replay those without) → explore only around `git diff <qa.lastApproved>..<sha>`.~~ Superseded by D20. |
| D6 | 3 | Wait guard applies to `atdd:qa` only (identified by `agent_type`; coverage-qa is deleted, D10). Refuses foreground Bash with `timeout` > 120000 ms, a shell loop containing `sleep`, or a lone `sleep` > 30 s. Background commands are allowed. The playbook's health poll moves to background + Monitor. |
| D7 | 4 | ~~Shared `.atdd/qa-harness/`~~ dropped with D11. Throwaway tooling stays in the round's evidence folder. The playbook keeps "use the app's test clock if `qa.notes` names one". |
| D8 | 5 | `duplication`, `architecture`, `dead-code`, `locked-files` move into `FAST_CHECKS`; any that measures > 10 s on a stack stays in `full` for that stack. |
| D9 | 7 | No retry logic. A null `agent()` result still ends the run as `needs-human`. Item 7 is dropped. |
| D10 | 2 | `coverage-qa` is deleted everywhere: agent file, cover prep's `hasQa`, `cover.js`, the cover skill text, README, guard `READ_ONLY`, `tests/workflows/cover-test.mjs`. |
| D11 | 4 | No QA harness requirement: init doesn't ask for or scan for clock control, interval overrides or fault injection. Rule instead: a scenario or check that would make QA wait more than 2 minutes in real time is skipped and reported to the human. The wait guard (D6) enforces it; its refusal message says so. |
| D12 | 6 | A **flaky test** fails now, was green before and its system under test is unchanged. No re-run: a failure that doesn't meet all three is a plain `fail`. |
| D13 | 6 | "Green before" = the test file exists unchanged on the base commit (the base branch is assumed green). |
| D14 | 6 | "System under test unchanged" = no production file changed on the branch is reachable from the test through the import graph (dependency-cruiser for TS/Next.js, the Python import graph, project references for .NET). Acceptance tests reach nearly everything, so their failures nearly always stay `fail`. |
| D15 | 6 | Flaky tests are excluded from the gate's coverage and mutation runs (whose tools refuse to start on a failing test), and those checks name the excluded tests. |
| D16 | 6 | Same classification in `fast` (commit hook) and `full`. A flaky test is advisory, never blocking; the pipeline never quarantines or skips a test, a human does. |
| D17 | 4 | The 2-minute limit is per single wait, which is what the guard can see. One wait over 2 minutes skips the scenario. |
| D18 | 4 | After every QA run the workflow `log()`s one line: "QA round N: pass/fail", plus the skipped scenarios no earlier QA run in this build reported (each with its reason), or "nothing new skipped". The QA result gets a structured `skipped` list and the workflow remembers which were reported. The run never stops for this. The final report lists every skipped scenario under `unverified`, separate from advisory findings. No push or OS notification, no mid-run return. |
| D19 | #12 | QA is never skipped. With QA last, its last tested commit always failed, so the production-changed skip can't apply. The quality stage reports no `productionChanged`. ADR-0002 is rewritten accordingly. |
| D20 | #12 | A QA re-run re-tests earlier findings by hand, replays every saved replay script with one command (hand-testing only the scenarios without one), then explores only around `git diff <qa.lastTested>..<sha>`. `build.js` records `memory.qa.lastTested` after every QA run and names it in the prompt. |
| D21 | #12 | Every QA run saves one self-contained replay script per scenario (`.hurl` for API scenarios, Playwright for UI scenarios when it's available, otherwise a step list) in `.atdd/<key>/qa/scenarios/`, overwritten each round. Findings evidence stays in `round-<n>/`. |
| D22 | #12 | A replay failure of a scenario that passed before is a blocker, with the replay output as evidence. A script that can't run again because of leftover state is fixed by QA and that scenario hand-tested; that is not a finding. |
| D23 | #12 | No QA memory across `/atdd:build` runs. |

## Open

None. Design tree closed 2026-11-13, awaiting the user's go-ahead to implement.
