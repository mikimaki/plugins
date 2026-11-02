// atdd quality gate: writes Monocart's merged V8 coverage as Cobertura XML for diff-cover and gate.py.
// Used by scripts/atdd/coverage.mjs.
//
// A line counts as executed when any of it ran. Its condition coverage is the share of the branches that
// start on it and ran, or, when no such branch missed, the share of its pieces that ran (the `.b` of `a?.b`
// is a piece). As with every V8-based tool, a branch is code of its own: the consequent of an `if`, either
// side of `?:`, the right of `&&`/`||`/`??`, a `case`; the missing `else` of an `if` isn't one. The same code
// reaches the report from several builds (Vitest, the Next.js server, the browser), so a branch counts as
// taken when any build took it: branches reported at the same offset are one branch, and a branch whose
// code ran according to the merged byte counts was taken.
import { mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'

const escapeXml = text => String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

function lineStarts(source) {
  const starts = [0]
  for (let index = source.indexOf('\n'); index !== -1; index = source.indexOf('\n', index + 1)) starts.push(index + 1)
  return starts
}

function lineAt(starts, offset) {
  let low = 0
  let high = starts.length - 1
  while (low < high) {
    const middle = Math.ceil((low + high) / 2)
    if (starts[middle] <= offset) low = middle
    else high = middle - 1
  }
  return low + 1
}

function neverRan(bytes) {
  const unexecuted = bytes.filter(range => range.count === 0).sort((a, b) => a.start - b.start)
  return (start, end) => {
    let reached = start
    for (const range of unexecuted) {
      if (range.start > reached) break
      reached = Math.max(reached, range.end)
      if (reached >= end) return true
    }
    return false
  }
}

function branchesByLine(file) {
  const starts = lineStarts(file.source ?? '')
  const unexecuted = neverRan(file.data.bytes ?? [])
  const taken = new Map()
  for (const branch of (file.data.branches ?? []).filter(b => !b.none)) {
    const ran = branch.count > 0 || (branch.end > branch.start && !unexecuted(branch.start, branch.end))
    taken.set(branch.start, (taken.get(branch.start) ?? false) || ran)
  }
  const byLine = new Map()
  for (const [offset, ran] of taken) {
    const line = lineAt(starts, offset)
    const tally = byLine.get(line) ?? { covered: 0, total: 0 }
    tally.total += 1
    if (ran) tally.covered += 1
    byLine.set(line, tally)
  }
  return byLine
}

function conditions(count, branches) {
  if (branches && branches.covered < branches.total) return branches
  if (typeof count === 'string') {
    const [covered, total] = count.split('/').map(Number)
    return { covered, total }
  }
  return branches
}

function describeLines(file) {
  const branches = branchesByLine(file)
  return Object.entries(file.data.lines ?? {}).map(([number, count]) => {
    const line = Number(number)
    return { line, hits: typeof count === 'string' ? 1 : count, conditions: conditions(count, branches.get(line)) }
  })
}

const rate = (covered, total) => (total === 0 ? '1' : (covered / total).toFixed(4))

function lineXml({ line, hits, conditions: c }) {
  if (!c) return `<line number="${line}" hits="${hits}" branch="false"/>`
  const percent = Math.floor((100 * c.covered) / c.total)
  return `<line number="${line}" hits="${hits}" branch="true" condition-coverage="${percent}% (${c.covered}/${c.total})"/>`
}

function classXml(file, totals) {
  const lines = describeLines(file)
  const covered = lines.filter(line => line.hits > 0).length
  const branches = lines.reduce((sum, line) => sum + (line.conditions?.total ?? 0), 0)
  const branchesCovered = lines.reduce((sum, line) => sum + (line.conditions?.covered ?? 0), 0)
  totals.lines += lines.length
  totals.linesCovered += covered
  totals.branches += branches
  totals.branchesCovered += branchesCovered
  return `<class name="${escapeXml(path.basename(file.sourcePath))}" filename="${escapeXml(file.sourcePath)}" `
    + `line-rate="${rate(covered, lines.length)}" branch-rate="${rate(branchesCovered, branches)}"><methods/><lines>`
    + lines.map(lineXml).join('') + '</lines></class>'
}

export default class CoberturaReporter {
  constructor(reportOptions, globalOptions) {
    this.outputFile = path.resolve(globalOptions.outputDir, reportOptions.outputFile ?? 'cobertura-coverage.xml')
    this.root = globalOptions.baseDir ?? process.cwd()
  }

  async generate(reportData) {
    const totals = { lines: 0, linesCovered: 0, branches: 0, branchesCovered: 0 }
    const classes = reportData.files.map(file => classXml(file, totals))
    const lineRate = rate(totals.linesCovered, totals.lines)
    const branchRate = rate(totals.branchesCovered, totals.branches)
    const xml = '<?xml version="1.0" ?>\n'
      + `<coverage lines-valid="${totals.lines}" lines-covered="${totals.linesCovered}" line-rate="${lineRate}" `
      + `branches-valid="${totals.branches}" branches-covered="${totals.branchesCovered}" branch-rate="${branchRate}" `
      + `timestamp="${Date.now()}" complexity="0" version="atdd">\n`
      + `<sources><source>${escapeXml(this.root)}</source></sources>\n`
      + `<packages><package name="atdd" line-rate="${lineRate}" branch-rate="${branchRate}"><classes>\n`
      + classes.join('\n') + '\n</classes></package></packages>\n</coverage>\n'
    mkdirSync(path.dirname(this.outputFile), { recursive: true })
    writeFileSync(this.outputFile, xml)
    return { reportPath: this.outputFile }
  }
}
