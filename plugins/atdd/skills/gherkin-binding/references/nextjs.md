# Binding Gherkin in a Next.js app

**Requirements:** Node 22 or later; TypeScript 6.0.x (see `typescript.md`); a MySQL test database (see the quality-gate skill's `references/nextjs.md`).

**Which runner:** playwright-bdd for every scenario. The scenarios run against the production build of the app (`next build` + `next start`), which Playwright starts with the test database:
- UI behavior goes through a real browser;
- route handlers go through Playwright's `request` fixture.

Vitest with React Testing Library covers the units: rules, queries, server actions, components.

## Wiring

`features/support/fixtures.ts` (from the gate templates) extends playwright-bdd's `test` and exports the `Given`, `When` and `Then` every step file uses. Add per-scenario state there as fixtures, never as module-level variables: Playwright runs scenarios in parallel.

```ts
// features/support/fixtures.ts
import { createBdd, test as base } from 'playwright-bdd'
import { recordBrowserCoverage } from '../../scripts/atdd/coverage.mjs'

type Member = { id: number; name: string }

type Fixtures = {
  recordBrowserCoverage: void
  members: Map<string, Member>
}

export const test = base.extend<Fixtures>({
  recordBrowserCoverage: [recordBrowserCoverage, { auto: true }],
  members: async ({}, provide) => provide(new Map()),
})

export const { Given, When, Then } = createBdd(test)
```

Name the fixture callback `provide`, not `use`: eslint-config-next's hook rules read `use(...)` as React's `use`.

**Steps** sit in `features/steps/`, one file per area, and talk the scenario's language. Page interactions go in small helpers next to the steps:

```ts
import { expect, type APIRequestContext, type Page } from '@playwright/test'
import { Given, Then, When } from '../support/fixtures'

async function registerMember(request: APIRequestContext, name: string) {
  const response = await request.post('/api/members', { data: { name } })
  expect(response.status()).toBe(201)
  return (await response.json()) as { id: number; name: string }
}

async function setDailyStepGoal(page: Page, memberId: number, steps: string) {
  const goalPage = `/members/${memberId}/goal`
  await page.goto(goalPage)
  await page.getByLabel('Daily step goal').fill(steps)
  const saved = page.waitForResponse(
    response => response.request().method() === 'POST' && new URL(response.url()).pathname === goalPage,
  )
  await page.getByRole('button', { name: 'Save goal' }).click()
  await saved
}

Given('{word} is a member without a daily step goal', async ({ request, members }, name: string) => {
  members.set(name, await registerMember(request, name))
})

When('{word} sets a daily step goal of {string} steps', async ({ page, members }, name: string, steps: string) => {
  await setDailyStepGoal(page, members.get(name)!.id, steps)
})

Then("{word}'s daily step goal is {string} steps", async ({ page, members }, name: string, steps: string) => {
  await page.goto(`/members/${members.get(name)!.id}/goal`)
  await expect(page.getByText(`Your daily step goal is ${steps} steps`)).toBeVisible()
})

Then('{word} is told {string}', async ({ page }, _name: string, message: string) => {
  await expect(page.getByRole('main').getByRole('alert')).toHaveText(message)
})
```

## Next.js specifics

- **Wait for a server action before moving on.** A form bound to a server action posts to the page's own URL. Wait for that POST response, as `setDailyStepGoal` does, before the next `goto` or the next step. Otherwise the navigation can cancel the action, and the scenario fails at random.
- **Find elements the way users do:** `getByRole`, `getByLabel`, `getByText`. Never CSS classes or test ids the user can't see.
- **Scope alerts to the page content.** Next.js renders a route announcer with `role="alert"` on every page, so a bare `getByRole('alert')` matches two elements. Use `page.getByRole('main').getByRole('alert')`.
- **Arrange Given state through the app.** Use its route handlers via the `request` fixture, or its pages. When the app has no interface for some state, seed it with a `PrismaClient` the test helper creates for `testDatabaseUrl()` from `test/test-database.ts`, the database the server uses. Don't import the app's `src/lib/db.ts`: it imports `server-only`, which throws outside Next.js, and it would read the development `DATABASE_URL`.
- **Data isolation:** both runners reset the test database once per run. Scenarios run in parallel against it, so each creates its own data (its own members, its own orders) and never relies on an empty table or a row another scenario made.
- **Time.** The browser's clock is `page.clock` (`await page.clock.setFixedTime(new Date('2026-03-01T09:00:00Z'))`). The server runs in its own process, so a scenario can't move its clock. Keep time decisions in functions that take `now` and pin them with unit tests. Scenarios then use dates relative to the real day, or a time the whole run fixes through `webServer.env` in `playwright.config.ts`, read by the one module the app gets the time from.
- **Never use** `test.fixme`, `test.fail` or `test.skip` (the gate counts them as suppressions), playwright-bdd's `@skip`/`@fixme` tags, or `missingSteps: 'skip-scenario'`.

## Unit and component tests (Vitest)

Tests sit next to the code as `*.test.ts(x)` and run in Node against the test database. `vitest.setup.ts` mocks `server-only`, so server modules import cleanly.

**Rules and queries:** plain tests. Queries hit the real MySQL test database, each test creating its own rows.

**Server actions:** call them like functions, with a `FormData`, and mock only the Next.js runtime they touch:

```ts
import { revalidatePath } from 'next/cache'
import { expect, it, vi } from 'vitest'
import { registerMember } from '@/features/members/members'
import { setDailyStepGoal } from './actions'
import { findDailyStepGoal } from './queries'

vi.mock('next/cache', () => ({ revalidatePath: vi.fn() }))

const form = (dailySteps: string) => {
  const data = new FormData()
  data.set('dailySteps', dailySteps)
  return data
}

it('setDailyStepGoal_GoalInRange_SavesItAndRefreshesTheGoalPage', async () => {
  const member = await registerMember('Robin')
  expect(await setDailyStepGoal(member.id, { message: null }, form('8,000'))).toEqual({ message: null })
  expect(await findDailyStepGoal(member.id)).toBe(8_000)
  expect(revalidatePath).toHaveBeenCalledWith(`/members/${member.id}/goal`)
})
```

**Async Server Components:** call and await them like functions. `notFound()` and `redirect()` throw errors whose message carries the outcome:

```ts
it('GoalPage_UnknownMember_IsNotFound', async () => {
  await expect(GoalPage({ params: Promise.resolve({ memberId: '987654' }) })).rejects.toThrow(
    'NEXT_HTTP_ERROR_FALLBACK;404',
  )
})
```

**Client Components:** opt into jsdom per file, render with React Testing Library and drive them with user-event. Mock the server-action module they call:

```tsx
// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { GoalForm } from './goal-form'

vi.mock('./actions', () => ({
  setDailyStepGoal: vi.fn(async () => ({ message: 'Enter a whole number of steps between 1,000 and 50,000' })),
}))

it('GoalForm_RefusedGoal_ShowsTheMessage', async () => {
  render(<GoalForm memberId={1} />)
  await userEvent.type(screen.getByLabelText('Daily step goal'), '999')
  await userEvent.click(screen.getByRole('button', { name: 'Save goal' }))
  expect((await screen.findByRole('alert')).textContent).toBe('Enter a whole number of steps between 1,000 and 50,000')
})
```

Mutation testing runs these Vitest tests only, never the scenarios. Every rule a scenario checks also needs a unit test that would fail if the rule broke.

## Commands (typical values for `.claude/atdd.json`)

```bash
npx bddgen && npx playwright test   # commands.acceptanceTests
npx vitest run                      # commands.unitTests
```
