export const meta = {
  name: 'cover',
  description: 'Cover behavior the code already has with acceptance tests bound to approved Gherkin scenarios, changing nothing but tests, then run coverage reviewer → QA → test-quality gate, sending blocking findings back to the acceptance-test-coverer until all three pass',
  whenToUse: 'After /atdd:gherkin produced approved scenarios for behavior the platform already has, when you want them as acceptance tests (a safety net for later refactoring) without any change to the production code. Pass the issue reference as args, e.g. "PROJ-45".',
  phases: [
    { title: 'Prepare', detail: 'check the scenarios, the test paths and the branch; commit the scenarios' },
    { title: 'Cover', detail: 'acceptance-test-coverer: step definitions against the existing code, then commit' },
    { title: 'Review', detail: 'coverage-reviewer: only tests changed, honest steps, a safety net worth trusting' },
    { title: 'QA', detail: 'coverage-qa: replay every scenario against the running app' },
    { title: 'Quality', detail: 'coverage-quality-tester: test-code gate, acceptance coverage and mutation' },
  ],
}

const input = typeof args === 'string' ? { issue: args } : (args || {})
const ref = String(input.issue || input.ref || input.issueRef || '').trim()
if (!ref) {
  return { status: 'needs-input', message: 'Give the issue reference, e.g. /atdd:cover PROJ-45' }
}

const STRINGS = { type: 'array', items: { type: 'string' } }
const FINDING = {
  type: 'object',
  required: ['id', 'severity', 'title', 'detail'],
  properties: {
    id: { type: 'string' },
    severity: { type: 'string', enum: ['blocker', 'major', 'minor', 'nit'] },
    audience: { type: 'string', enum: ['coverer', 'human'] },
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
    testPaths: STRINGS,
    unfinishedWork: STRINGS,
    maxRounds: { type: 'integer' },
    hasQa: { type: 'boolean' },
  },
}
const COVERAGE = {
  type: 'object',
  required: ['sha', 'summary', 'targets', 'testsRun', 'responses', 'discrepancies', 'blocked'],
  properties: {
    sha: { type: 'string' },
    summary: { type: 'string' },
    targets: STRINGS,
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
    discrepancies: {
      type: 'array',
      items: {
        type: 'object',
        required: ['scenario', 'step', 'expected', 'actual'],
        properties: {
          scenario: { type: 'string' },
          step: { type: 'string' },
          expected: { type: 'string' },
          actual: { type: 'string' },
          codeLocation: { type: 'string' },
          evidence: { type: 'string' },
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
          status: { type: 'string', enum: ['pass', 'fail', 'skipped', 'warn'] },
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
  { key: 'review', phase: 'Review', agentType: 'atdd:coverage-reviewer', name: 'coverage reviewer' },
  { key: 'qa', phase: 'QA', agentType: 'atdd:coverage-qa', name: 'QA' },
  { key: 'quality', phase: 'Quality', agentType: 'atdd:coverage-quality-tester', name: 'quality gate' },
]

const isBlocking = f => f.severity === 'blocker' || f.severity === 'major'
const forHuman = f => f.audience === 'human'
const json = value => JSON.stringify(value, null, 2)
const targetsFile = prep => `.atdd/${prep.key}/cover/targets.txt`

function context(prep) {
  return [
    `Issue ${prep.key}: "${prep.title}". The scenarios describe behavior the code already has: this run adds acceptance tests and changes nothing else.`,
    `Spec (source of truth): ${prep.specPath}`,
    `Approved scenarios: ${prep.featureDir} (last committed in ${prep.scenariosCommit}).`,
    `Branch ${prep.branch}; the work under test is git diff ${prep.base}..HEAD (base = merge-base with the base branch).`,
    `Test paths, the only files this work may change: ${prep.testPaths.join(', ')}.`,
    `Coverage targets file: ${targetsFile(prep)}.`,
  ].join('\n')
}

function coverPrompt(prep, round, maxRounds, failed) {
  const lines = [context(prep), `Round ${round} of at most ${maxRounds}.`]
  if (!failed) {
    if ((prep.unfinishedWork || []).length) {
      lines.push(`Uncommitted test files from an earlier run are in the working tree: ${prep.unfinishedWork.join(', ')}. Start from them.`)
    }
    lines.push('Cover the scenarios as your instructions describe: bind every scenario to the code as it is, write the coverage targets file, run the cover fast checks, commit, and report the new commit SHA. Where the code does something other than what a scenario says, change neither: report the discrepancies.')
  } else {
    lines.push(`The ${failed.stage.name} stage found blocking problems in commit ${failed.sha}. Fix every finding in the tests, never in the production code, or dispute it with evidence. Run the cover fast checks, commit, and report the new SHA with a response per finding id. If an honest fix makes a scenario fail against the code, report that as a discrepancy.`)
    lines.push(json(failed.findings))
  }
  return lines.join('\n\n')
}

function stagePrompt(stage, prep, round, sha, memory) {
  const lines = [context(prep), `Round ${round}. Commit under test: ${sha} (checked out in the working tree).`]
  if (stage.key === 'review' && memory.lastApproved) {
    lines.push(`You approved ${memory.lastApproved} in an earlier round. Focus on git diff ${memory.lastApproved}..${sha} and whatever it affects, but check again that nothing outside the test paths changed on the whole branch.`)
  }
  if (stage.key === 'qa') lines.push(`Evidence directory: .atdd/${prep.key}/cover/qa/round-${round}/`)
  if (stage.key === 'quality') {
    lines.push(`Run the gate with ATDD_BASE=${prep.base} ATDD_SCENARIOS_COMMIT=${prep.scenariosCommit} ATDD_COVER_TARGETS=${targetsFile(prep)} ATDD_OUT=.atdd/${prep.key}/cover/quality/round-${round}`)
  }
  if (memory.findings.length) {
    lines.push('Your blocking findings from your last run, and the coverer\'s responses. Judge each one in priorFindings (resolved, dispute-accepted or still-open), checking the tests or the app yourself; a still-open one must also appear again in findings:')
    lines.push(json({ findings: memory.findings, responses: memory.responses }))
  }
  lines.push('Work as your instructions describe and report. verdict must be "fail" if any finding is a blocker or major. Set audience "human" only on a finding no change to the tests can resolve; a blocking one stops the run for a human instead of going back to the coverer.')
  return lines.join('\n\n')
}

phase('Prepare')
const prep = await agent(`Mode: cover. Issue reference: ${ref}`, { label: 'prepare', phase: 'Prepare', agentType: 'atdd:preparer', schema: PREP })
if (!prep) return { status: 'needs-human', stage: 'prepare', reason: 'The preparer did not finish.' }
if (!prep.ok) return { status: 'needs-human', stage: 'prepare', reason: prep.reason || 'Preparation failed.' }
if (!(prep.testPaths || []).length) return { status: 'needs-human', stage: 'prepare', reason: 'The preparer reported no test paths; set gate.testDirs or cover.testPaths in .claude/atdd.json.' }

const stages = STAGES.filter(s => s.key !== 'qa' || prep.hasQa)
if (!prep.hasQa) log('No commands.qaUp in .claude/atdd.json, so the QA stage is skipped. Run /atdd:init to set it up.')

const maxRounds = Math.max(1, prep.maxRounds || 5)
const memory = Object.fromEntries(stages.map(s => [s.key, { findings: [], responses: [], lastApproved: null }]))
const history = []
const advisory = {}
let failed = null
let sha = null
let targets = []

function report(status, extra) {
  return {
    status,
    issue: prep.key,
    title: prep.title,
    branch: prep.branch,
    base: prep.base,
    sha,
    testPaths: prep.testPaths,
    targets,
    rounds: history.length ? history[history.length - 1].round : 0,
    history,
    advisory: Object.values(advisory).flat(),
    ...extra,
  }
}

const humanNext = `Resolve the findings outside the tests yourself. A change to production code, configuration or packages belongs in a change of its own on the base branch; update this branch from it, then re-run /atdd:cover ${ref}.`

for (let round = 1; round <= maxRounds; round++) {
  const coverage = await agent(coverPrompt(prep, round, maxRounds, failed), {
    label: `cover · round ${round}`,
    phase: 'Cover',
    agentType: 'atdd:acceptance-test-coverer',
    schema: COVERAGE,
  })
  if (!coverage) return report('needs-human', { stage: 'cover', reason: 'The acceptance-test-coverer did not finish.', outstanding: failed ? failed.findings : [] })
  if (coverage.targets.length) targets = coverage.targets
  if (coverage.discrepancies.length) {
    return report('discrepancies', {
      stage: 'cover',
      reason: `In ${coverage.discrepancies.length} place(s) the code doesn't do what the scenarios say. This workflow changes neither the code nor the scenarios, so a human decides which one is right.`,
      discrepancies: coverage.discrepancies,
      coverSummary: coverage.summary,
      ...(coverage.blocked ? { alsoBlocked: coverage.blockedReason || 'The coverer also reported it is blocked.' } : {}),
      next: `For each discrepancy decide which side is wrong. A wrong scenario: fix the spec and re-run /atdd:gherkin ${ref}, or edit the scenarios yourself. Wrong code (a bug): fix it in a change of its own on the base branch, then update this branch from it. Then re-run /atdd:cover ${ref}; the coverer continues from its uncommitted test files.`,
    })
  }
  if (coverage.blocked) {
    return report('needs-human', { stage: 'cover', reason: coverage.blockedReason || 'The acceptance-test-coverer reported it is blocked.', outstanding: failed ? failed.findings : [], next: humanNext })
  }
  if (!coverage.sha) return report('needs-human', { stage: 'cover', reason: 'The acceptance-test-coverer reported no commit.' })
  if (failed && coverage.sha === failed.sha) {
    log(`Round ${round}: the coverer made no new commit; its responses go back to the ${failed.stage.name} for judgment.`)
  }
  sha = coverage.sha
  if (failed) {
    const ids = new Set(failed.findings.map(f => f.id))
    memory[failed.stage.key].responses = coverage.responses.filter(r => ids.has(r.findingId))
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

    if (blocking.some(forHuman)) {
      log(`Round ${round}: the ${stage.name} raised findings only a human can resolve.`)
      return report('needs-human', {
        stage: stage.key,
        reason: `The ${stage.name} raised ${blocking.filter(forHuman).length} blocking finding(s) that no change to the tests can resolve.`,
        outstanding: blocking,
        next: humanNext,
      })
    }
    if (blocking.length) {
      failed = { stage, sha, findings: blocking }
      log(`Round ${round}: ${stage.name} raised ${blocking.length} blocking finding(s); back to the acceptance-test-coverer.`)
      break
    }
    log(`Round ${round}: ${stage.name} passed.`)
  }

  if (!failed) {
    return report('done', {
      next: `All checks passed on ${sha}, and the branch changes nothing outside the test paths. Read the diff (git diff ${prep.base}..${sha}), then push the branch and open a PR. The advisory findings list behavior no scenario covers yet: add it to the spec before refactoring that code.`,
    })
  }
}

return report('needs-human', {
  stage: failed.stage.key,
  reason: `Still failing after ${maxRounds} round(s).`,
  outstanding: failed.findings,
  next: `Read the outstanding findings. Fix or overrule them yourself (in the tests only), then re-run /atdd:cover ${ref} to continue from the current commit.`,
})
