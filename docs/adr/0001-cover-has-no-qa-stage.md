# /atdd:cover has no QA stage

`/atdd:cover` changes nothing but tests, so manual QA of the running app would test code nobody touched. The coverage reviewer (honest step definitions) and the cover gate's acceptance-mutation check (the tests notice when the production code they claim to cover is broken) already prove the tests exercise the real system. We removed `coverage-qa` from `/atdd:cover` and `/atdd:cover-queue` rather than keep a slow stage that can't find a defect in the change.
