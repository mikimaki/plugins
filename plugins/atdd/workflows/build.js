export const meta = {
  name: 'build',
  description: 'Implement approved Gherkin scenarios (ATDD), then run reviewer → QA → quality gate, sending blocking findings back to the implementer until all three pass',
  whenToUse: 'After /atdd:gherkin produced approved scenarios and you have read them. Pass the same issue reference as args, e.g. "PROJ-45".',
  phases: [
    { title: 'Prepare', detail: 'check the scenarios and commit them' },
    { title: 'Implement', detail: 'implementer: step definitions + code, then commit' },
    { title: 'Review', detail: 'reviewer: architecture, standards, honest step definitions' },
    { title: 'QA', detail: 'qa: run the app and test every scenario by hand' },
    { title: 'Quality', detail: 'quality-tester: mutation, complexity, coverage and more' },
  ],
}

const input = typeof args === 'string' ? { issue: args } : (args || {})
const ref = String(input.issue || input.ref || input.issueRef || '').trim()
if (!ref) {
  return { status: 'needs-input', message: 'Give the issue reference, e.g. /atdd:build PROJ-45' }
}

const STRINGS = { type: 'array', items: { type: 'string' } }
const FINDING = {
  type: 'object',
  required: ['id', 'severity', 'title', 'detail'],
  properties: {
    id: { type: 'string' },
    severity: { type: 'string', enum: ['blocker', 'major', 'minor', 'nit'] },
    file: { type: 'string' },
    location: { type: 'string' },
    title: { type: 'string' },
    detail: { type: 'string' },
    evidence: { type: 'string' },
    suggestion: { type: 'string' },
  },
}
const PREP = {
  type: 'object',
  required: ['ok'],
  properties: {
    ok: { type: 'boolean' },
    reason: { type: 'string' },
    key: { type: 'string' },
    title: { type: 'string' },
    specPath: { type: 'string' },
    featureDir: { type: 'string' },
    branch: { type: 'string' },
    base: { type: 'string' },
    scenariosCommit: { type: 'string' },
    maxRounds: { type: 'integer' },
    hasQa: { type: 'boolean' },
  },
}
const IMPLEMENTATION = {
  type: 'object',
  required: ['sha', 'summary', 'testsRun', 'responses', 'blocked'],
  properties: {
    sha: { type: 'string' },
    summary: { type: 'string' },
    testsRun: STRINGS,
    responses: {
      type: 'array',
      items: {
        type: 'object',
        required: ['findingId', 'status', 'note'],
        properties: {
          findingId: { type: 'string' },
          status: { type: 'string', enum: ['fixed', 'disputed'] },
          note: { type: 'string' },
        },
      },
    },
    blocked: { type: 'boolean' },
    blockedReason: { type: 'string' },
  },
}
const CHECK = {
  type: 'object',
  required: ['verdict', 'findings', 'summary'],
  properties: {
    verdict: { type: 'string', enum: ['pass', 'fail'] },
    findings: { type: 'array', items: FINDING },
    results: {
      type: 'array',
      items: {
        type: 'object',
        required: ['name', 'status'],
        properties: {
          name: { type: 'string' },
          status: { type: 'string', enum: ['pass', 'fail', 'skipped'] },
          detail: { type: 'string' },
        },
      },
    },
    priorFindings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'verdict'],
        properties: {
          id: { type: 'string' },
          verdict: { type: 'string', enum: ['resolved', 'dispute-accepted', 'still-open'] },
          note: { type: 'string' },
        },
      },
    },
    summary: { type: 'string' },
  },
}

const STAGES = [
  { key: 'review', phase: 'Review', agentType: 'atdd:reviewer', name: 'reviewer' },
  { key: 'qa', phase: 'QA', agentType: 'atdd:qa', name: 'QA' },
  { key: 'quality', phase: 'Quality', agentType: 'atdd:quality-tester', name: 'quality gate' },
]

const isBlocking = f => f.severity === 'blocker' || f.severity === 'major'
const json = value => JSON.stringify(value, null, 2)

function context(prep) {
  return [
    `Issue ${prep.key}: "${prep.title}".`,
    `Spec (source of truth): ${prep.specPath}`,
    `Approved scenarios: ${prep.featureDir} (last committed in ${prep.scenariosCommit}).`,
    `Branch ${prep.branch}; the work under test is git diff ${prep.base}..HEAD (base = merge-base with the base branch).`,
  ].join('\n')
}

function implementPrompt(prep, round, maxRounds, failed) {
  const lines = [context(prep), `Round ${round} of at most ${maxRounds}.`]
  if (!failed) {
    lines.push('Implement the issue acceptance-test first, as your instructions describe. Commit when the fast checks pass, and report the new commit SHA.')
  } else {
    lines.push(`The ${failed.stage.name} stage found blocking problems in commit ${failed.sha}. Fix every finding below or dispute it with evidence, run the fast checks, commit, and report the new SHA with a response per finding id.`)
    lines.push(json(failed.findings))
  }
  return lines.join('\n\n')
}

function stagePrompt(stage, prep, round, sha, memory) {
  const lines = [context(prep), `Round ${round}. Commit under test: ${sha} (checked out in the working tree).`]
  if (stage.key === 'review' && memory.lastApproved) {
    lines.push(`You approved ${memory.lastApproved} in an earlier round. Focus on git diff ${memory.lastApproved}..${sha} and whatever it affects; don't re-open code you already approved unless the new change breaks it.`)
  }
  if (stage.key === 'qa') lines.push(`Evidence directory: .atdd/${prep.key}/qa/round-${round}/`)
  if (stage.key === 'quality') {
    lines.push(`Run the gate with ATDD_BASE=${prep.base} ATDD_SCENARIOS_COMMIT=${prep.scenariosCommit} ATDD_OUT=.atdd/${prep.key}/quality/round-${round}`)
  }
  if (memory.findings.length) {
    lines.push('Your blocking findings from your last run, and the implementer\'s responses. Judge each one in priorFindings (resolved, dispute-accepted or still-open), checking the code or the app yourself; a still-open one must also appear again in findings:')
    lines.push(json({ findings: memory.findings, responses: memory.responses }))
  }
  lines.push('Work as your instructions describe and report. verdict must be "fail" if any finding is a blocker or major.')
  return lines.join('\n\n')
}

phase('Prepare')
const prep = await agent(`Mode: build. Issue reference: ${ref}`, { label: 'prepare', phase: 'Prepare', agentType: 'atdd:preparer', schema: PREP })
if (!prep) return { status: 'needs-human', stage: 'prepare', reason: 'The preparer did not finish.' }
if (!prep.ok) return { status: 'needs-human', stage: 'prepare', reason: prep.reason || 'Preparation failed.' }

const stages = STAGES.filter(s => s.key !== 'qa' || prep.hasQa)
if (!prep.hasQa) log('No commands.qaUp in .claude/atdd.json, so the QA stage is skipped. Run /atdd:init to set it up.')

const maxRounds = Math.max(1, prep.maxRounds || 5)
const memory = Object.fromEntries(stages.map(s => [s.key, { findings: [], responses: [], lastApproved: null }]))
const history = []
const advisory = {}
let failed = null
let sha = null

function report(status, extra) {
  return {
    status,
    issue: prep.key,
    title: prep.title,
    branch: prep.branch,
    base: prep.base,
    sha,
    rounds: history.length ? history[history.length - 1].round : 0,
    history,
    advisory: Object.values(advisory).flat(),
    ...extra,
  }
}

for (let round = 1; round <= maxRounds; round++) {
  const implementation = await agent(implementPrompt(prep, round, maxRounds, failed), {
    label: `implement · round ${round}`,
    phase: 'Implement',
    agentType: 'atdd:implementer',
    schema: IMPLEMENTATION,
  })
  if (!implementation) return report('needs-human', { stage: 'implement', reason: 'The implementer did not finish.', outstanding: failed ? failed.findings : [] })
  if (implementation.blocked) {
    return report('needs-human', { stage: 'implement', reason: implementation.blockedReason || 'The implementer reported it is blocked.', outstanding: failed ? failed.findings : [] })
  }
  if (!implementation.sha) return report('needs-human', { stage: 'implement', reason: 'The implementer reported no commit.' })
  if (failed && implementation.sha === failed.sha) {
    log(`Round ${round}: the implementer made no new commit; its responses go back to the ${failed.stage.name} for judgment.`)
  }
  sha = implementation.sha
  if (failed) {
    const ids = new Set(failed.findings.map(f => f.id))
    memory[failed.stage.key].responses = implementation.responses.filter(r => ids.has(r.findingId))
  }

  failed = null
  for (const stage of stages) {
    const result = await agent(stagePrompt(stage, prep, round, sha, memory[stage.key]), {
      label: `${stage.key} · round ${round}`,
      phase: stage.phase,
      agentType: stage.agentType,
      schema: CHECK,
    })
    if (!result) return report('needs-human', { stage: stage.key, reason: `The ${stage.name} agent did not finish.` })

    const blocking = result.findings.filter(isBlocking).map(f => ({ ...f, id: f.id.startsWith(`${stage.key}-`) ? f.id : `${stage.key}-${f.id}` }))
    advisory[stage.key] = result.findings.filter(f => !isBlocking(f)).map(f => ({ ...f, stage: stage.key }))
    history.push({ round, stage: stage.key, sha, blocking: blocking.length, summary: result.summary })
    memory[stage.key].findings = blocking
    memory[stage.key].responses = []
    if (stage.key === 'review' && blocking.length === 0) memory.review.lastApproved = sha

    if (blocking.length) {
      failed = { stage, sha, findings: blocking }
      log(`Round ${round}: ${stage.name} raised ${blocking.length} blocking finding(s); back to the implementer.`)
      break
    }
    log(`Round ${round}: ${stage.name} passed.`)
  }

  if (!failed) {
    return report('done', {
      next: `All checks passed on ${sha}. Read the diff (git diff ${prep.base}..${sha}), then push the branch and open a PR. Advisory findings are listed for follow-up.`,
    })
  }
}

return report('needs-human', {
  stage: failed.stage.key,
  reason: `Still failing after ${maxRounds} round(s).`,
  outstanding: failed.findings,
  next: `Read the outstanding findings. Fix or overrule them yourself, then re-run /atdd:build ${ref} to continue from the current commit.`,
})
