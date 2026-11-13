# ATDD pipeline

The atdd plugin turns a spec into approved Gherkin, then into tested code (`/atdd:build`) or into tests for code that already exists (`/atdd:cover`), checked by independent agents.

## Language

### Pipeline

**Stage**:
One independent check in a build round (review, quality, QA), run by its own agent; a blocking finding from any stage sends the work back to the implementer.
_Avoid_: step, phase (a phase is only the progress grouping)

**Round**:
One implementer commit followed by the stages that judge it.
_Avoid_: iteration, cycle

**Last approved commit**:
The most recent commit a given stage passed without blocking findings; later rounds of that stage look only at what changed since.
_Avoid_: baseline, last green

**Last tested commit**:
The most recent commit a given stage judged, whatever its verdict; QA's later rounds explore what changed since.
_Avoid_: last run, previous commit

**Replay script**:
An executable record of one scenario as QA performed it against the running app, which a later QA round runs again instead of re-testing that scenario by hand.
_Avoid_: evidence (evidence also covers findings), regression test

**Idle wait**:
Time QA spends after acting in a scenario during which the outcome depends only on a scheduled trigger in the app firing (a cron job, timer, delay, expiry or poll interval), not on work in progress.
_Avoid_: long wait, real-time wait

### Code under test

**Test paths**:
The folders holding tests and approved scenarios; everything a cover run is allowed to change.
_Avoid_: test dirs (that is one config key that feeds it)

**Production code**:
Every file in the repository outside the test paths and the pipeline workspace, including manifests, configuration, migrations and build files.
_Avoid_: source, src

### Quality gate

**Fast gate**:
The checks that run on every implementer commit, cheap enough to block the commit itself.
_Avoid_: pre-commit checks

**Flaky test**:
A test that fails although it passed on the base and nothing it exercises changed on the branch; reported, never blocking.
_Avoid_: intermittent test, unstable test

**Full gate**:
The fast gate plus the slow checks (mutation, diff coverage, vulnerabilities), run by the quality stage.
_Avoid_: quality checks
