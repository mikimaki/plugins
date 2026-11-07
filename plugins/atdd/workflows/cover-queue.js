export const meta = {
  name: 'cover-queue',
  description: 'Run /atdd:cover over existing Gherkin one .feature file at a time: each file is a unit on its own branch, stacked on the last unit that passed every check, so later units reuse earlier step definitions. Parks units that end in discrepancies or need a human, stops on problems every unit would hit, optionally pushes each passing unit and opens a PR against its stack parent, and reports per unit',
  whenToUse: 'When reviewed Gherkin for many areas already exists on a branch (the source ref) and you want it covered unattended, one PR per .feature file. Args: the source ref, then the areas (folders under featuresDir) or units (<area>/<file>.feature) in order, and optionally --max-units N, --open-prs, --resume-parked. Example: "claude/inventory a01-accounts a03-catalogue --max-units 5 --open-prs".',
  phases: [
    { title: 'Plan', detail: 'derive each unit\'s status and the top of the stack from git; set up the integration branch' },
    { title: 'Units', detail: 'per unit: /atdd:cover on its own stacked branch, then publish (done) or park (failed)' },
    { title: 'Report', detail: 'per unit status, branch, PR, parked reason; per area, requirements no done unit covers' },
  ],
}

const AGENT_CAP = 1000
const UNIT_WORST_CASE = 24 // prepare + 5 rounds x (coverer + 3 stages) + publish or park, with margin

function parseArgs(raw) {
  if (raw && typeof raw === 'object') {
    return {
      source: String(raw.source || raw.ref || '').trim(),
      areas: [].concat(raw.areas || []).map(String),
      units: [].concat(raw.units || []).map(String),
      maxUnits: raw.maxUnits ? Number(raw.maxUnits) : null,
      openPrs: Boolean(raw.openPrs),
      resumeParked: Boolean(raw.resumeParked),
    }
  }
  const out = { source: '', areas: [], units: [], maxUnits: null, openPrs: false, resumeParked: false }
  const tokens = String(raw || '').trim().split(/\s+/).filter(Boolean)
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i]
    const [flag, value] = token.split('=')
    if (flag === '--max-units' || flag === '--maxUnits') out.maxUnits = Number(value !== undefined ? value : tokens[++i])
    else if (token === '--open-prs') out.openPrs = true
    else if (token === '--resume-parked') out.resumeParked = true
    else if (flag === '--source') out.source = value !== undefined ? value : tokens[++i]
    else if (!out.source) out.source = token
    else if (token.endsWith('.feature')) out.units.push(token)
    else out.areas.push(token)
  }
  return out
}

const opts = parseArgs(args)
if (!opts.source || (!opts.areas.length && !opts.units.length)) {
  return {
    status: 'needs-input',
    message: 'Give the source ref and the areas or units in order, e.g. /atdd:cover-queue claude/inventory a01-accounts a03-catalogue --max-units 5 --open-prs',
  }
}
if (opts.maxUnits !== null && !(opts.maxUnits > 0)) {
  return { status: 'needs-input', message: '--max-units needs a positive number.' }
}
if (typeof workflow !== 'function') {
  return { status: 'stopped', reason: 'This Claude Code has no nested workflows (workflow()), which /atdd:cover-queue needs to run /atdd:cover per unit.' }
}

const STRINGS = { type: 'array', items: { type: 'string' } }
const UNIT = {
  type: 'object',
  required: ['key', 'file', 'feature', 'branch', 'status'],
  properties: {
    key: { type: 'string' },
    file: { type: 'string' },
    feature: { type: 'string' },
    branch: { type: 'string' },
    status: { type: 'string', enum: ['pending', 'interrupted', 'done', 'parked'] },
    merged: { type: 'boolean' },
    parent: { type: 'string' },
    pr: { type: 'string' },
    reason: { type: 'string' },
    scenarios: { type: 'integer' },
    checkErrors: STRINGS,
  },
}
const PLAN = {
  type: 'object',
  required: ['ok', 'problems', 'units'],
  properties: {
    ok: { type: 'boolean' },
    problems: STRINGS,
    notes: STRINGS,
    sourceSha: { type: 'string' },
    integrationBranch: { type: 'string' },
    integrationCreated: { type: 'string' },
    remote: { type: 'string' },
    openPrs: { type: 'boolean' },
    top: { type: 'string' },
    maxConsecutiveParks: { type: 'integer' },
    pluginRoot: { type: 'string' },
    units: { type: 'array', items: UNIT },
  },
}
const PARK = {
  type: 'object',
  required: ['ok'],
  properties: {
    ok: { type: 'boolean' },
    parkCommit: { type: 'string' },
    committedFiles: STRINGS,
    leftovers: STRINGS,
    reason: { type: 'string' },
  },
}
const PUBLISH = {
  type: 'object',
  required: ['ok', 'recorded'],
  properties: {
    ok: { type: 'boolean' },
    recorded: { type: 'boolean' },
    pushed: STRINGS,
    prUrl: { type: 'string' },
    error: { type: 'string' },
  },
}
const SUMMARY = {
  type: 'object',
  required: ['areas'],
  properties: {
    areas: {
      type: 'array',
      items: {
        type: 'object',
        required: ['key', 'complete'],
        properties: {
          key: { type: 'string' },
          complete: { type: 'boolean' },
          units: { type: 'integer' },
          done: { type: 'integer' },
          parked: { type: 'integer' },
          uncoveredRequirements: STRINGS,
        },
      },
    },
  },
}

let agentsUsed = 0
const housekeeping = (prompt, label, schema) => {
  agentsUsed++
  return agent(prompt, { label, phase: label === 'plan' ? 'Plan' : label === 'summary' ? 'Report' : 'Units', agentType: 'atdd:preparer', schema })
}

const helperArgs = [
  `--source ${opts.source}`,
  ...opts.areas.map(a => `--area ${a}`),
  ...opts.units.map(u => `--unit ${u}`),
  ...(opts.openPrs ? ['--open-prs'] : []),
].join(' ')

phase('Plan')
const plan = await housekeeping(`Mode: cover-queue-plan.\nHelper arguments: ${helperArgs}`, 'plan', PLAN)
if (!plan) return { status: 'stopped', reason: 'The preparer did not finish planning the queue.' }
if (!plan.ok) {
  return { status: 'stopped', stage: 'plan', reason: 'The queue can\'t start.', problems: plan.problems, notes: plan.notes || [] }
}
for (const note of plan.notes || []) log(note)
if (plan.integrationCreated) log(`Created the integration branch ${plan.integrationBranch} from ${plan.integrationCreated}.`)

const integration = plan.integrationBranch
const remote = plan.remote || 'origin'
const openPrs = Boolean(plan.openPrs)
const maxParks = Math.max(1, plan.maxConsecutiveParks || 3)
const named = new Set(opts.units.map(u => u.replace(/^.*?([^/]+\/[^/]+\.feature)$/, '$1')))
const isNamed = u => named.has(`${u.key}/${u.file}`)

async function runCover(unitArgs) {
  try {
    return await workflow('atdd:cover', unitArgs)
  } catch (error) {
    if (!plan.pluginRoot) throw error
    log(`workflow('atdd:cover') failed (${String(error && error.message || error)}); running ${plan.pluginRoot}/workflows/cover.js instead.`)
    return workflow({ scriptPath: `${plan.pluginRoot}/workflows/cover.js` }, unitArgs)
  }
}

function prBody(unit, result, parent) {
  const metrics = (result.metrics || []).filter(m => /^acceptance-|^duplicate-steps|^production-untouched/.test(m.name))
  const gaps = (result.advisory || []).filter(f => f.audience === 'human' || f.stage === 'quality')
  const stages = [...new Set((result.history || []).map(h => h.stage))]
  return [
    `Covers \`${unit.feature}\` (${result.scenarioCount || unit.scenarios || '?'} scenario(s)) with acceptance tests bound to the code as it is. Nothing outside the test paths changes.`,
    '',
    `Unit of \`/atdd:cover-queue\`, stacked on \`${parent}\`: this PR shows only this unit's change. Merge the stack bottom-up into \`${integration}\`, with merge commits.`,
    '',
    `**Checks:** ${stages.join(', ') || 'none recorded'} passed on ${result.sha} after ${result.rounds} round(s).`,
    '',
    '**Coverage of the protected code** (the acceptance tests alone, on the coverage targets):',
    ...(metrics.length ? metrics.map(m => `- ${m.name}: ${m.status}${m.detail ? ` — ${m.detail.split('\n')[0]}` : ''}`) : ['- not reported']),
    ...((result.targets || []).length ? ['', `**Coverage targets:** ${result.targets.map(t => `\`${t}\``).join(', ')}`] : []),
    '',
    '**Advisory spec gaps** (behavior no scenario covers yet; worth adding to the spec before refactoring this code):',
    ...(gaps.length ? gaps.slice(0, 25).map(f => `- ${f.title}${f.file ? ` (\`${f.file}\`)` : ''}`) : ['- none reported']),
    ...(gaps.length > 25 ? [`- … and ${gaps.length - 25} more`] : []),
    '',
    'Generated by /atdd:cover-queue (Claude Code).',
  ].join('\n')
}

async function publish(unit, result, parent) {
  const lines = [
    'Mode: cover-publish.',
    `Unit branch: ${unit.branch}`,
    `Stack parent (the PR base): ${parent}`,
    `Commit that passed every check: ${result.sha || ''}`,
    `Remote: ${remote}`,
    `openPrs: ${openPrs}`,
  ]
  if (openPrs) {
    lines.push(`PR title: test(${unit.key}): acceptance tests for ${unit.file}`)
    lines.push(`PR body file: ${result.workDir || `.atdd/${unit.key}/cover/${unit.file.replace(/\.feature$/, '')}`}/pr-body.md`)
    lines.push(`PR body (write it to the file verbatim):\n<<<BODY\n${prBody(unit, result, parent)}\nBODY>>>`)
  }
  return housekeeping(lines.join('\n'), `publish · ${unit.key}/${unit.file}`, PUBLISH)
}

async function park(unit, reason) {
  return housekeeping([
    'Mode: cover-park.',
    `Unit branch: ${unit.branch}`,
    `Unit: ${unit.feature}`,
    `Reason (record it verbatim, one line): ${reason.replace(/\s+/g, ' ').slice(0, 300)}`,
  ].join('\n'), `park · ${unit.key}/${unit.file}`, PARK)
}

function failureReason(result) {
  if (!result) return 'The /atdd:cover run did not finish.'
  if (result.status === 'discrepancies') {
    const items = (result.discrepancies || []).map(d => `${d.scenario}: expects ${d.expected}; the code ${d.actual}`)
    return `discrepancies: ${items.join(' | ') || result.reason}`
  }
  if (result.status === 'needs-human') return `needs-human (${result.stage || '?'}): ${result.reason || ''}`
  return `${result.status}: ${result.reason || ''}`
}

phase('Units')
let top = plan.top || integration
let ran = 0
let parksInARow = 0
let stopped = null
const rows = []

for (const unit of plan.units) {
  const row = { unit: `${unit.key}/${unit.file}`, branch: unit.branch, status: unit.status, parent: unit.parent || '', pr: unit.pr || '', parkedReason: '' }
  rows.push(row)
  if (unit.status === 'done') {
    row.status = unit.merged ? 'done (merged)' : 'done (already)'
    if (!unit.merged && openPrs && !unit.pr && !stopped) {
      const published = await publish(unit, { sha: '', rounds: 0, history: [], scenarioCount: unit.scenarios }, unit.parent || integration)
      row.pr = (published && published.prUrl) || ''
      if (published && published.error) row.publishError = published.error
    }
    continue
  }
  const runnable = unit.status === 'pending' || unit.status === 'interrupted' || (unit.status === 'parked' && (opts.resumeParked || isNamed(unit)))
  if (!runnable) {
    row.status = 'parked (earlier)'
    row.parkedReason = unit.reason || ''
    continue
  }
  if (stopped) { row.status = 'not run'; row.parkedReason = 'the queue stopped'; continue }
  if (opts.maxUnits && ran >= opts.maxUnits) { row.status = 'not run'; row.parkedReason = `--max-units ${opts.maxUnits} reached`; continue }
  if ((unit.checkErrors || []).length) {
    row.status = 'invalid scenarios'
    row.parkedReason = unit.checkErrors.slice(0, 5).join('; ')
    log(`${row.unit}: its scenarios fail the Gherkin check; skipped. Fix them on ${opts.source}.`)
    continue
  }
  if (agentsUsed + UNIT_WORST_CASE + 2 > AGENT_CAP) {
    stopped = `the run is close to the ${AGENT_CAP}-agent limit of one workflow; re-run the queue to continue`
    row.status = 'not run'
    row.parkedReason = stopped
    continue
  }

  ran++
  log(`${row.unit}: ${unit.status === 'pending' ? 'starting' : `resuming (${unit.status})`} on ${unit.branch}, stacked on ${top}.`)
  let result = null
  let crash = ''
  try {
    result = await runCover({ issue: unit.key, unit: { source: opts.source, feature: unit.feature, branch: unit.branch, stackTop: top } })
  } catch (error) {
    crash = String(error && error.message || error)
  }
  agentsUsed += (result && result.agentCalls) || UNIT_WORST_CASE
  row.parent = (result && result.parent) || top

  if (result && result.status === 'done') {
    const published = await publish(unit, result, row.parent)
    row.status = 'done'
    row.sha = result.sha
    row.rounds = result.rounds
    row.pr = (published && published.prUrl) || ''
    if (!published || !published.recorded) {
      stopped = `${row.unit} passed, but recording it as done failed${published && published.error ? `: ${published.error}` : ''}; the next unit can't be stacked on it safely`
    } else if (published.error) {
      row.publishError = published.error
      log(`${row.unit}: done, but publishing failed: ${published.error}`)
    }
    top = unit.branch
    parksInARow = 0
    continue
  }

  const reason = crash ? `the /atdd:cover run failed: ${crash}` : failureReason(result)
  const parked = await park(unit, reason)
  row.status = 'parked'
  row.parkedReason = reason
  if (result && result.discrepancies) row.discrepancies = result.discrepancies
  if (result && result.outstanding) row.outstanding = result.outstanding.map(f => `${f.id}: ${f.title}`)
  parksInARow++
  if (!parked || !parked.ok) {
    stopped = `parking ${row.unit} failed${parked && parked.reason ? `: ${parked.reason}` : ''}`
  } else if ((parked.leftovers || []).length) {
    stopped = `parking ${row.unit} left files outside the test paths, so the next unit can't start from a clean tree: ${parked.leftovers.join(', ')}`
  } else if (!result || crash || result.scope === 'repo') {
    stopped = `${row.unit} hit a problem every unit would hit: ${reason}`
  } else if (parksInARow >= maxParks) {
    stopped = `${parksInARow} units in a row were parked (cover.maxConsecutiveParks is ${maxParks}); check the setup before running more`
  }
  if (stopped) log(`Stopping the queue: ${stopped}`)
}

phase('Report')
const touchedAreas = [...new Set(plan.units.map(u => u.key))]
const summary = await housekeeping(`Mode: cover-queue-summary.\nHelper arguments: --source ${opts.source} ${touchedAreas.map(a => `--area ${a}`).join(' ')}`, 'summary', SUMMARY)

const counts = rows.reduce((acc, r) => { const k = r.status.split(' ')[0]; acc[k] = (acc[k] || 0) + 1; return acc }, {})
const status = stopped ? 'stopped' : rows.some(r => r.status === 'not run') ? 'partial' : 'finished'
return {
  status,
  ...(stopped ? { stopReason: stopped } : {}),
  source: opts.source,
  integrationBranch: integration,
  top,
  openPrs,
  ran,
  counts,
  units: rows,
  areas: (summary && summary.areas) || [],
  next: [
    rows.some(r => r.status === 'done') ? `Review the PRs bottom-up (the first targets ${integration}) and merge each into its base with a merge commit; GitHub retargets the next PR when a merged branch is deleted. The human's final PR takes ${integration} into the base branch.` : '',
    rows.some(r => r.status === 'parked' || r.status === 'parked (earlier)') ? `Parked units keep their test files in a WIP commit on their branch. Decide each parked reason (fix the scenario on ${opts.source}, or the code in a change of its own on the base branch), then resume with /atdd:cover-queue ${opts.source} <area>/<file>.feature --resume-parked.` : '',
    stopped ? `Fix what stopped the queue, then re-run the same command: done units are skipped.` : '',
  ].filter(Boolean),
}
