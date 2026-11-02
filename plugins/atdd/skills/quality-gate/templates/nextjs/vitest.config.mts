import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'
import { coverageDir, unitCoverageOptions } from './scripts/atdd/coverage.mjs'
import { testDatabaseUrl } from './test/test-database.ts'

export default defineConfig({
  plugins: [react()],
  resolve: { tsconfigPaths: true },
  test: {
    include: ['src/**/*.test.{ts,tsx}'],
    environment: 'node',
    setupFiles: ['./vitest.setup.ts'],
    globalSetup: ['./test/reset-database.ts'],
    env: { DATABASE_URL: testDatabaseUrl() },
    coverage: {
      enabled: coverageDir !== null,
      provider: 'custom',
      customProviderModule: 'vitest-monocart-coverage',
      ...(coverageDir && { coverageReportOptions: unitCoverageOptions() }),
    },
  },
})
