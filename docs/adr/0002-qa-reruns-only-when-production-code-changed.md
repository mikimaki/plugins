# QA re-runs only when production code changed

In `/atdd:build` the stages run review → quality → QA, and a later QA round runs only when the diff since QA's last approved commit touches production code, defined as anything outside the test paths and `.atdd/` (a deny-list, so manifests, config and migrations count without being listed). The quality stage reports that fact deterministically, because the workflow script can't run git and the implementer has a stake in the answer. When QA does re-run, it re-tests its earlier findings, replays every scenario from its saved executable evidence, and narrows only the exploratory testing to the diff.

## Considered Options

- An allow-list built from `gate.sourceDirs` plus known manifests: rejected, because any production file it forgets silently skips QA.
- Letting QA choose which scenarios the diff can affect: rejected, because a wrong guess lets a regression through; replaying saved evidence is cheap.
