import path from 'node:path'
import { defineConfig, devices } from '@playwright/test'
import { defineBddConfig } from 'playwright-bdd'
import { coverageDir } from './scripts/atdd/coverage.mjs'
import { testDatabaseUrl } from './test/test-database'

const port = Number(process.env.ACCEPTANCE_PORT ?? 3100)
const serverCoverage = coverageDir ? `NODE_V8_COVERAGE=${JSON.stringify(path.join(coverageDir, 'node-v8'))} ` : ''

const testDir = defineBddConfig({
  features: 'features/**/*.feature',
  steps: ['features/steps/**/*.ts', 'features/support/**/*.ts'],
  missingSteps: 'fail-on-gen',
})

export default defineConfig({
  testDir,
  fullyParallel: true,
  forbidOnly: true,
  retries: 0,
  reporter: [['list']],
  globalSetup: './test/reset-database.ts',
  use: { baseURL: `http://localhost:${port}`, trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `npm run build && ${serverCoverage}npm run start -- --port ${port}`,
    url: `http://localhost:${port}/api/health`,
    reuseExistingServer: false,
    timeout: 300_000,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 15_000 },
    env: { DATABASE_URL: testDatabaseUrl(), ...(coverageDir && { ATDD_COVERAGE: '1' }) },
  },
})
