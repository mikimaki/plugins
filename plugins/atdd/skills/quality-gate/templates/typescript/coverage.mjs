// atdd quality gate: coverage for TypeScript and Next.js. While ATDD_COVERAGE_DIR is set, every place the code
// runs records native V8 coverage under it:
//   unit/      Vitest, through vitest-monocart-coverage (unitCoverageOptions in vitest.config.mts)
//   node-v8/   Node processes started with NODE_V8_COVERAGE: Cucumber (gate.py sets it), the Next.js server
//              (playwright.config.ts sets it)
//   browser/   Chromium, through the recordBrowserCoverage Playwright fixture (Next.js)
// `node scripts/atdd/coverage.mjs merge` then folds them into one Cobertura report at
// $ATDD_COVERAGE_DIR/merged/cobertura-coverage.xml for gate.py: the same line is covered when any of them ran it.
// Without ATDD_COVERAGE_DIR (normal test runs) nothing is recorded.
import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { CoverageReport } from 'monocart-coverage-reports'

export const coverageDir = process.env.ATDD_COVERAGE_DIR ? path.resolve(process.env.ATDD_COVERAGE_DIR) : null

const root = process.cwd()
const sourceDirs = (JSON.parse(readFileSync(path.join(root, '.claude', 'atdd.json'), 'utf8')).gate?.sourceDirs ?? ['src'])
  .map(dir => dir.replace(/^\.\/|\/+$/g, ''))
const NOT_PRODUCTION = /(^|\/)(generated|__tests__|__mocks__)\/|\.(test|spec)\.[cm]?[jt]sx?$|\.d\.[cm]?ts$/
const BUNDLED_SOURCE = /^(?:turbopack|webpack):\/\/\/?(?:\[project\]|[^/]*)\/(?:\.\/)?(.+)$/
const NEXT_STATIC_CHUNK = /^https?:\/\/[^/]+\/_next\/(static\/.+\.js)$/
const reporter = fileURLToPath(new URL('./cobertura-reporter.mjs', import.meta.url))
const MERGED_REPORT = path.join('..', 'merged', 'cobertura-coverage.xml')

// Source maps name files by bundler URLs (turbopack:///[project]/src/a.tsx, webpack://_N_E/./src/a.tsx);
// the report needs the repo path, src/a.tsx.
function repoPath(sourcePath, info) {
  const bundled = info?.url?.match(BUNDLED_SOURCE)
  return bundled ? bundled[1] : sourcePath.replace(/^\[project\]\//, '')
}

const isProduction = sourcePath => sourceDirs.some(dir => sourcePath.startsWith(`${dir}/`))
  && !NOT_PRODUCTION.test(sourcePath)
  && existsSync(path.join(root, sourcePath))

// By the time the report is merged, the Next.js server that served the browser's chunks has stopped.
function sourceMapResolver(url, defaultResolver) {
  const chunk = url.match(NEXT_STATIC_CHUNK)
  const map = chunk && path.join(root, '.next', `${chunk[1]}.map`)
  return map && existsSync(map) ? JSON.parse(readFileSync(map, 'utf8')) : defaultResolver(url)
}

function options(part, extra) {
  return {
    name: `atdd ${part} coverage`,
    outputDir: path.join(coverageDir, part),
    baseDir: root,
    entryFilter: entry => !entry.url.includes('/node_modules/'),
    sourceFilter: sourcePath => !sourcePath.includes('node_modules'),
    sourcePath: repoPath,
    outputFilter: file => isProduction(file.sourcePath),
    sourceMapResolver,
    ...extra,
  }
}

export const unitCoverageOptions = () => options('unit', { reports: [['raw', { outputDir: 'raw' }]] })

const browserCoverageOptions = () => options('browser', { reports: ['none'] })

// Playwright auto fixture, `recordBrowserCoverage: [recordBrowserCoverage, { auto: true }]`. Chromium drops a
// document's coverage when the page loads another one, so the fixture saves what was recorded before every
// page.goto, reload, goBack and goForward. A full-page load the app starts by itself (an <a> to another
// document, window.location) still loses the coverage of the page it leaves.
export async function recordBrowserCoverage({ page, browserName }, provide) {
  if (coverageDir === null || browserName !== 'chromium') return provide()
  const report = new CoverageReport(browserCoverageOptions())
  const save = async () => {
    const entries = await page.coverage.stopJSCoverage()
    if (entries.length > 0) await report.add(entries)
  }
  for (const navigation of ['goto', 'reload', 'goBack', 'goForward']) {
    const navigate = page[navigation].bind(page)
    page[navigation] = async (...args) => {
      await save()
      await page.coverage.startJSCoverage({ resetOnNavigation: false })
      return navigate(...args)
    }
  }
  await page.coverage.startJSCoverage({ resetOnNavigation: false })
  await provide()
  await save()
}

async function merge() {
  const unitCoverage = path.join(coverageDir, 'unit', 'raw')
  const nodeCoverage = path.join(coverageDir, 'node-v8')
  const report = new CoverageReport(options('browser', {
    inputDir: existsSync(unitCoverage) ? [unitCoverage] : [],
    reports: [[reporter, { type: 'v8', outputFile: MERGED_REPORT }], 'console-summary'],
  }))
  if (existsSync(nodeCoverage)) await report.addFromDir(nodeCoverage)
  await report.generate()
}

if (process.argv[1] === fileURLToPath(import.meta.url) && process.argv[2] === 'merge') {
  if (coverageDir === null) throw new Error('Set ATDD_COVERAGE_DIR to the directory the test runs recorded coverage into.')
  await merge()
}
