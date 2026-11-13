# QA re-runs replay saved evidence instead of being skipped

In `/atdd:build` the stages run review → quality → QA, so a QA pass ends the run and every later QA round in a run follows a QA failure. A re-run therefore can't be skipped. Instead it is made cheaper. In every run QA saves one self-contained replay script per scenario (`.hurl` for API scenarios, Playwright for UI ones) in `.atdd/<key>/qa/scenarios/`. A later round works in three steps. First, QA re-tests its earlier findings by hand. Second, it replays the saved scripts with one command, and hand-tests only scenarios that have no script. Third, it explores only around `git diff <last tested commit>..<sha>`. A replay failure of a scenario that passed before is a blocker.

## Considered Options

- Skip QA when the diff since its last tested commit touches no production code (the earlier version of this ADR, and mikimaki/plugins#6): rejected. That commit always failed QA with blocking findings, so a test-only diff means the app that failed is unchanged, and skipping would ship known failures.
- Skip the scenarios the diff can't affect: rejected, because choosing them is a guess and a wrong guess lets a regression through.
- Re-test every scenario by hand each round: rejected, because replaying a saved script checks the same real app for a fraction of the cost.
- Run the replay outside the QA agent (a gate subcommand run by another agent): rejected, because the replay needs the running app, which only QA starts.
