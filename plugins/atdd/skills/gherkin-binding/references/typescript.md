# Binding Gherkin in TypeScript

**Requirements:**
- Node 22 or later.
- Pin TypeScript 6.0.x for now: typescript-eslint and eslint-plugin-sonarjs don't support TypeScript 7 yet.

**Which runner:**
- **APIs and services:** Cucumber.js (`@cucumber/cucumber`).
- **UI features tested in a browser:** playwright-bdd.

## Cucumber.js (API / service)

```bash
npm i -D @cucumber/cucumber tsx supertest @types/supertest
```

`cucumber.mjs` (ESM project):

```js
export default {
  paths: ['features/**/*.feature'],                  // = featuresDir/**
  import: ['./tsx-register.js', 'features/steps/**/*.ts'],
  format: ['progress', ['message', 'reports/cucumber.ndjson'], ['junit', 'reports/cucumber.xml']],
  strict: true,
}
```

`tsx-register.js`:

```js
import { register } from 'tsx/esm/api'
register()
```

For CommonJS projects, run it as `npx cucumber-js --require-module tsx/cjs --require 'features/steps/**/*.ts'`.

**Strictness:**
- A normal run always fails on undefined, ambiguous or failed steps.
- `strict: true` (the default) also fails on pending steps.
- `--dry-run` never fails. To check binding without running, grep its message output: `"status":"UNDEFINED"` or `"status":"AMBIGUOUS"` means a missing or clashing binding.

**Per-scenario state** lives in a custom World:

```ts
import { setWorldConstructor, World, IWorldOptions } from '@cucumber/cucumber'
import request, { type Response } from 'supertest'
import { buildApp } from '../../src/app'          // the real composition root
import { FakeClock } from './support/fake-clock'

export class AppWorld extends World {
  clock = new FakeClock(new Date('2026-01-01T00:00:00Z'))
  app = buildApp({ clock: this.clock })           // fake only boundaries: clock, external HTTP (msw/nock)
  http = request(this.app)
  user?: { id: string }
  lastResponse?: Response
  constructor(options: IWorldOptions) { super(options) }
}
setWorldConstructor(AppWorld)
```

**Steps:**

```ts
import { Given, When, Then } from '@cucumber/cucumber'
import assert from 'node:assert/strict'
import type { AppWorld } from './world'

Given('a user with the display name {string}', async function (this: AppWorld, displayName: string) {
  const res = await this.http.post('/users').send({ displayName }).expect(201)
  this.user = res.body
})

When('the user changes their display name to {string}', async function (this: AppWorld, displayName: string) {
  this.lastResponse = await this.http.patch(`/users/${this.user!.id}`).send({ displayName })
})

Then('the user is told {string}', function (this: AppWorld, message: string) {
  assert.equal(this.lastResponse!.body.message, message)
})

Then('the display name is still {string}', async function (this: AppWorld, displayName: string) {
  const res = await this.http.get(`/users/${this.user!.id}`).expect(200)
  assert.equal(res.body.displayName, displayName)
})
```

**External HTTP fakes:** use `msw` (node) or `nock`, never the app's own modules.

**Coverage:** `npx c8 --reporter=cobertura --reports-dir=coverage/acceptance npx cucumber-js`.

**Mutation testing** with `@stryker-mutator/cucumber-runner`: `"testRunner": "cucumber"`, and for tsx/ESM `"testRunnerNodeArgs": ["--import", "tsx"]`.

## playwright-bdd (UI)

```ts
// playwright.config.ts
import { defineConfig } from '@playwright/test'
import { defineBddConfig } from 'playwright-bdd'

const testDir = defineBddConfig({
  features: 'features/**/*.feature',
  steps: 'features/steps/**/*.ts',
  missingSteps: 'fail-on-gen',          // default; bddgen exits 1 on a missing step
})
export default defineConfig({ testDir, use: { baseURL: 'http://localhost:5173' } })
```

```bash
npx bddgen && npx playwright test
```

- Page interactions belong in page objects. The steps stay at the level of the scenario's sentences.
- Never use `missingSteps: 'skip-scenario'`.

## Commands (typical values for `.claude/atdd.json`)

```bash
npx cucumber-js                     # commands.acceptanceTests  (or: npx bddgen && npx playwright test)
npx vitest run                      # commands.unitTests        (or jest/mocha, as the repo uses)
```
