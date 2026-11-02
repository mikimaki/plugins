// atdd quality gate: merge these settings into next.config.ts. While the gate measures coverage, playwright.config.ts
// builds the app with ATDD_COVERAGE=1, and the build ships source maps so coverage maps back to src/. Other builds
// are unchanged.
import type { NextConfig } from 'next'

const measuringCoverage = process.env.ATDD_COVERAGE === '1'

const nextConfig: NextConfig = {
  productionBrowserSourceMaps: measuringCoverage,
  experimental: { serverSourceMaps: measuringCoverage },
}

export default nextConfig
