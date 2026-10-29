export const meta = {
  name: 'gherkin',
  description: 'Turn an issue-tracker spec into reviewed Gherkin acceptance scenarios (gherkin-writer ⇄ gherkin-reviewer loop)',
  whenToUse: 'After a spec exists in the issue tracker (e.g. from /to-spec). Pass the issue reference as args, e.g. "PROJ-45", "#123" or a spec file path.',
  phases: [
    { title: 'Prepare', detail: 'read .claude/atdd.json, snapshot the spec, set up the branch' },
    { title: 'Write', detail: 'gherkin-writer drafts the requirements ledger and scenarios' },
    { title: 'Review', detail: 'gherkin-reviewer checks faithfulness, coverage and testability' },
  ],
}

const input = typeof args === 'string' ? { issue: args } : (args || {})
const ref = String(input.issue || input.ref || input.issueRef || '').trim()
if (!ref) {
  return { status: 'needs-input', message: 'Give the issue reference, e.g. /atdd:gherkin PROJ-45, /atdd:gherkin #123 or /atdd:gherkin .scratch/<feature>/spec.md' }
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
    source: { type: 'string' },
    featureDir: { type: 'string' },
    existingFiles: STRINGS,
    branch: { type: 'string' },
    glossary: { type: 'string' },
    architectureDocs: STRINGS,
    maxRounds: { type: 'integer' },
  },
}
const DRAFT = {
  type: 'object',
  required: ['files', 'requirements', 'scenarios', 'checkerErrors', 'openQuestions', 'responses', 'summary'],
  properties: {
    files: STRINGS,
    requirements: { type: 'integer' },
    scenarios: { type: 'integer' },
    checkerErrors: { type: 'integer' },
    openQuestions: STRINGS,
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
    summary: { type: 'string' },
  },
}
const REVIEW = {
  type: 'object',
  required: ['approved', 'findings', 'openQuestions', 'summary'],
  properties: {
    approved: { type: 'boolean' },
    findings: { type: 'array', items: FINDING },
    openQuestions: {
      type: 'array',
      items: {
        type: 'object',
        required: ['question', 'verdict'],
        properties: {
          question: { type: 'string' },
          verdict: { type: 'string', enum: ['genuine', 'answerable-from-spec'] },
          note: { type: 'string' },
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

const isBlocking = f => f.severity === 'blocker' || f.severity === 'major'
const json = value => JSON.stringify(value, null, 2)

// /atdd:build refuses to start while the ledger lists open questions, so one the spec already answers is a blocker in disguise.
const answerableQuestions = review => (review.openQuestions || [])
  .filter(q => q.verdict === 'answerable-from-spec')
  .map((q, i) => ({
    id: `OQ${i + 1}`,
    severity: 'major',
    file: 'requirements.md',
    location: 'Open questions',
    title: 'Open question the spec already answers',
    detail: q.note ? `${q.question}\nReviewer: ${q.note}` : q.question,
    suggestion: 'Turn the answer into a requirement and scenario, quoting the spec, and remove the question. Dispute it with a quote if the spec really leaves it open.',
  }))

function context(prep) {
  return [
    `Issue ${prep.key}: "${prep.title}".`,
    `Spec (source of truth): ${prep.specPath}, a git-ignored snapshot of ${prep.source || 'the issue'}.`,
    `Feature directory: ${prep.featureDir}`,
    `Glossary: ${prep.glossary || 'none'}. Architecture docs: ${(prep.architectureDocs || []).join(', ') || 'none'}.`,
  ].join('\n')
}

function writerPrompt(prep, round, maxRounds, blocking) {
  const lines = [
    context(prep),
    `The ledger's Source line must name ${prep.source || 'the issue'}, not the snapshot.`,
    `Round ${round} of at most ${maxRounds}.`,
  ]
  if (round === 1) {
    if ((prep.existingFiles || []).length) {
      lines.push(`The feature directory already has files from an earlier run or a human: ${prep.existingFiles.join(', ')}. Start from them.`)
    }
    lines.push('Write the requirements ledger and the scenarios as your instructions describe, run the checker until it reports no errors, and report.')
  } else {
    lines.push('The gherkin-reviewer returned these blocking findings. Address every one (fixed, or disputed with a quote from the spec), re-run the checker, and report a response per finding id.')
    lines.push(json(blocking))
  }
  return lines.join('\n\n')
}

function reviewerPrompt(prep, round, previousBlocking, draft) {
  const lines = [context(prep), `Review round ${round}.`]
  if (round > 1) {
    lines.push('Your blocking findings from the previous round, and the writer\'s responses. Judge each one (resolved, dispute-accepted or still-open) in priorFindings, verifying fixes in the files themselves:')
    lines.push(json({ findings: previousBlocking, responses: draft.responses }))
  }
  lines.push(`The writer reports ${draft.requirements} requirement(s), ${draft.scenarios} scenario(s), ${draft.checkerErrors} checker error(s) and these open questions: ${json(draft.openQuestions)}`)
  lines.push('Review as your instructions describe and report. approved must be false if any finding is a blocker or major.')
  return lines.join('\n\n')
}

function finish(status, prep, round, draft, review, outstanding) {
  const openQuestions = draft ? draft.openQuestions : []
  const next = {
    approved: `Read the scenarios (git diff -- ${prep.featureDir}), edit anything you disagree with, then run /atdd:build ${ref}.`,
    'open-questions': `The scenarios are approved, but the spec has open questions. Answer them in the spec (and re-run /atdd:gherkin ${ref}) or edit the ledger and scenarios yourself; /atdd:build refuses to start while the ledger lists open questions.`,
    'needs-human': `The writer and reviewer didn't converge in ${round} round(s). Read the outstanding findings, fix or overrule them in ${prep.featureDir}, then re-run /atdd:gherkin ${ref} or go straight to /atdd:build ${ref}.`,
  }[status]
  return {
    status,
    issue: prep.key,
    title: prep.title,
    branch: prep.branch,
    featureDir: prep.featureDir,
    files: draft ? draft.files : [],
    requirements: draft ? draft.requirements : 0,
    scenarios: draft ? draft.scenarios : 0,
    rounds: round,
    openQuestions,
    openQuestionAssessment: review ? review.openQuestions : [],
    outstanding,
    advisory: review ? review.findings.filter(f => !isBlocking(f)) : [],
    writerSummary: draft ? draft.summary : '',
    reviewerSummary: review ? review.summary : '',
    next,
  }
}

phase('Prepare')
const prep = await agent(`Mode: gherkin. Issue reference: ${ref}`, { label: 'prepare', phase: 'Prepare', agentType: 'atdd:preparer', schema: PREP })
if (!prep) return { status: 'needs-human', stage: 'prepare', reason: 'The preparer did not finish.' }
if (!prep.ok) return { status: 'needs-human', stage: 'prepare', reason: prep.reason || 'Preparation failed.' }

const maxRounds = Math.max(1, prep.maxRounds || 3)
let blocking = []
let draft = null
let review = null

for (let round = 1; round <= maxRounds; round++) {
  draft = await agent(writerPrompt(prep, round, maxRounds, blocking), {
    label: `write · round ${round}`,
    phase: 'Write',
    agentType: 'atdd:gherkin-writer',
    schema: DRAFT,
  })
  if (!draft) return { ...finish('needs-human', prep, round, null, review, blocking), reason: 'The gherkin-writer did not finish.' }

  review = await agent(reviewerPrompt(prep, round, blocking, draft), {
    label: `review · round ${round}`,
    phase: 'Review',
    agentType: 'atdd:gherkin-reviewer',
    schema: REVIEW,
  })
  if (!review) return { ...finish('needs-human', prep, round, draft, null, blocking), reason: 'The gherkin-reviewer did not finish.' }

  blocking = [...review.findings.filter(isBlocking), ...answerableQuestions(review)]
  log(`Round ${round}: ${draft.requirements} requirement(s), ${draft.scenarios} scenario(s), ${blocking.length} blocking finding(s)`)
  if (blocking.length === 0) {
    return finish(draft.openQuestions.length ? 'open-questions' : 'approved', prep, round, draft, review, [])
  }
}

return finish('needs-human', prep, maxRounds, draft, review, blocking)
