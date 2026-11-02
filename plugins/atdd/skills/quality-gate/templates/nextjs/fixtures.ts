import { createBdd, test as base } from 'playwright-bdd'
import { recordBrowserCoverage } from '../../scripts/atdd/coverage.mjs'

// Per-scenario state lives in fixtures; add the repo's own next to recordBrowserCoverage. Name the callback
// `provide`, not `use`: eslint-config-next reads `use(...)` as React's use hook.
type Fixtures = {
  recordBrowserCoverage: void
}

export const test = base.extend<Fixtures>({
  recordBrowserCoverage: [recordBrowserCoverage, { auto: true }],
})

export const { Given, When, Then } = createBdd(test)
