import { run } from './run.mjs'
import assert from 'node:assert/strict'
const B = process.argv[2]
const prep = { ok: true, key: 'PROJ-45', title: 'Goals', specPath: '.atdd/PROJ-45/spec.md', featureDir: 'features/PROJ-45/', branch: 'atdd/PROJ-45', base: 'b1', scenariosCommit: 's1', maxRounds: 3, hasQa: true }
const impl = sha => ({ sha, summary: '', testsRun: [], responses: [], blocked: false })
const pass = { verdict: 'pass', findings: [], summary: 'ok' }
const fail = id => ({ verdict: 'fail', findings: [{ id, severity: 'blocker', title: 'broken', detail: '' }], summary: 'no' })
const make = s => { const sc = JSON.parse(JSON.stringify(s)); return async (p, o) => sc[o.agentType].shift() }
const stagesOf = calls => calls.map(c => c.opts.agentType).filter(t => !['atdd:preparer', 'atdd:implementer'].includes(t))
// every round runs review, then quality, then QA
{
  const { result, calls } = await run(B, { args: 'PROJ-45', agent: make({ 'atdd:preparer': [prep], 'atdd:implementer': [impl('c1')], 'atdd:reviewer': [pass], 'atdd:quality-tester': [pass], 'atdd:qa': [pass] }) })
  assert.equal(result.status, 'done')
  assert.deepEqual(stagesOf(calls), ['atdd:reviewer', 'atdd:quality-tester', 'atdd:qa'])
  assert.deepEqual(calls.map(c => c.opts.phase).slice(2), ['Review', 'Quality', 'QA'])
  console.log('order ok')
}
// a quality failure goes back to the implementer before QA runs
{
  const { result, calls, logs } = await run(B, { args: 'PROJ-45', agent: make({ 'atdd:preparer': [prep], 'atdd:implementer': [impl('c1'), impl('c2')], 'atdd:reviewer': [pass, pass], 'atdd:quality-tester': [fail('dup'), pass], 'atdd:qa': [pass] }) })
  assert.equal(result.status, 'done')
  assert.deepEqual(calls.map(c => c.opts.agentType).slice(1), ['atdd:implementer', 'atdd:reviewer', 'atdd:quality-tester', 'atdd:implementer', 'atdd:reviewer', 'atdd:quality-tester', 'atdd:qa'])
  assert.match(calls[4].prompt, /quality gate stage found blocking problems in commit c1/)
  assert.match(calls[4].prompt, /quality-dup/)
  assert.ok(logs.includes('Round 1: quality gate raised 1 blocking finding(s); back to the implementer.'))
  console.log('quality stops round before QA ok')
}
// an agent that returns nothing ends the run as needs-human, with no retry
{
  const { result, calls } = await run(B, { args: 'PROJ-45', agent: make({ 'atdd:preparer': [prep], 'atdd:implementer': [impl('c1')], 'atdd:reviewer': [pass], 'atdd:quality-tester': [null], 'atdd:qa': [pass] }) })
  assert.equal(result.status, 'needs-human')
  assert.equal(result.stage, 'quality')
  assert.equal(result.reason, 'The quality gate agent did not finish.')
  assert.equal(calls.filter(c => c.opts.agentType === 'atdd:quality-tester').length, 1)
  assert.equal(calls.filter(c => c.opts.agentType === 'atdd:qa').length, 0)
  console.log('null result ok')
}
const qaPrompts = calls => calls.filter(c => c.opts.agentType === 'atdd:qa').map(c => c.prompt)
// the first QA run names no last tested commit, only the replay-script folder
{
  const { calls } = await run(B, { args: 'PROJ-45', agent: make({ 'atdd:preparer': [prep], 'atdd:implementer': [impl('c1')], 'atdd:reviewer': [pass], 'atdd:quality-tester': [pass], 'atdd:qa': [pass] }) })
  const [first] = qaPrompts(calls)
  assert.doesNotMatch(first, /last tested/i)
  assert.doesNotMatch(first, /git diff c1\.\./)
  assert.match(first, /\.atdd\/PROJ-45\/qa\/scenarios\//)
  console.log('first QA prompt ok')
}
// after a QA failure, QA runs again: it names the failed commit, the diff to the new commit and the playbook's later-rounds method
{
  const { result, calls } = await run(B, { args: 'PROJ-45', agent: make({ 'atdd:preparer': [prep], 'atdd:implementer': [impl('c1'), impl('c2')], 'atdd:reviewer': [pass, pass], 'atdd:quality-tester': [pass, pass], 'atdd:qa': [fail('login'), pass] }) })
  assert.equal(result.status, 'done')
  const prompts = qaPrompts(calls)
  assert.equal(prompts.length, 2)
  const [, second] = prompts
  assert.match(second, /You last tested c1\b/)
  assert.match(second, /"Later rounds"/)
  assert.match(second, /git diff c1\.\.c2/)
  assert.match(second, /\.atdd\/PROJ-45\/qa\/scenarios\//)
  assert.match(second, /qa-login/)
  for (const c of calls.filter(c => c.opts.agentType === 'atdd:quality-tester')) assert.doesNotMatch(c.prompt, /productionChanged|production-untouched/)
  console.log('QA re-run after QA failure ok')
}
// QA fails, then review and quality fail in later rounds: QA still names the commit it last tested
{
  const { result, calls } = await run(B, { args: 'PROJ-45', agent: make({
    'atdd:preparer': [{ ...prep, maxRounds: 4 }],
    'atdd:implementer': [impl('c1'), impl('c2'), impl('c3'), impl('c4')],
    'atdd:reviewer': [pass, fail('naming'), pass, pass],
    'atdd:quality-tester': [pass, fail('dup'), pass],
    'atdd:qa': [fail('login'), pass],
  }) })
  assert.equal(result.status, 'done')
  assert.equal(result.sha, 'c4')
  const prompts = qaPrompts(calls)
  assert.equal(prompts.length, 2)
  assert.match(prompts[1], /You last tested c1\b/)
  assert.match(prompts[1], /git diff c1\.\.c4/)
  assert.doesNotMatch(prompts[1], /\bc[23]\b/)
  console.log('QA last tested survives review and quality failures ok')
}
const skip = (scenario, reason) => ({ scenario, reason })
const qaPass = (...skipped) => ({ ...pass, skipped })
const qaFail = (id, ...skipped) => ({ ...fail(id), skipped })
// QA reports skipped scenarios, each with its reason; the other stages don't
{
  const { calls } = await run(B, { args: 'PROJ-45', agent: make({ 'atdd:preparer': [prep], 'atdd:implementer': [impl('c1')], 'atdd:reviewer': [pass], 'atdd:quality-tester': [pass], 'atdd:qa': [qaPass()] }) })
  const { schema } = calls.find(c => c.opts.agentType === 'atdd:qa').opts
  assert.ok(schema.required.includes('skipped'))
  assert.deepEqual(schema.properties.skipped.items.required, ['scenario', 'reason'])
  for (const c of calls.filter(c => ['atdd:reviewer', 'atdd:quality-tester'].includes(c.opts.agentType))) assert.equal(c.opts.schema.properties.skipped, undefined)
  console.log('QA skipped schema ok')
}
const qaLines = logs => logs.filter(l => /: QA /.test(l))
// one line per QA run: its verdict and only the scenarios no earlier QA run skipped; skipping doesn't fail the run
{
  const digest = skip('Nightly digest is sent', 'idle wait: nightly cron at 02:00 (~11h)')
  const lockout = skip('Locked account opens again', 'idle wait: 15-minute lockout expiry')
  const { result, logs } = await run(B, { args: 'PROJ-45', agent: make({
    'atdd:preparer': [prep],
    'atdd:implementer': [impl('c1'), impl('c2'), impl('c3')],
    'atdd:reviewer': [pass, pass, pass],
    'atdd:quality-tester': [pass, pass, pass],
    'atdd:qa': [qaFail('login', digest), qaFail('login', digest, lockout), qaPass(digest, lockout)],
  }) })
  assert.equal(result.status, 'done')
  assert.deepEqual(qaLines(logs), [
    'Round 1: QA raised 1 blocking finding(s); back to the implementer. Newly skipped: "Nightly digest is sent" (idle wait: nightly cron at 02:00 (~11h)).',
    'Round 2: QA raised 1 blocking finding(s); back to the implementer. Newly skipped: "Locked account opens again" (idle wait: 15-minute lockout expiry).',
    'Round 3: QA passed. Nothing new skipped.',
  ])
  console.log('QA log lines ok')
}
// the final report lists every scenario skipped across the build once, as unverified, apart from advisory findings
{
  const digest = skip('Nightly digest is sent', 'idle wait: nightly cron at 02:00 (~11h)')
  const lockout = skip('Locked account opens again', 'idle wait: 15-minute lockout expiry')
  const nit = { verdict: 'pass', findings: [{ id: 'typo', severity: 'nit', title: 'typo', detail: '' }], summary: 'ok', skipped: [digest] }
  const done = await run(B, { args: 'PROJ-45', agent: make({ 'atdd:preparer': [prep], 'atdd:implementer': [impl('c1'), impl('c2')], 'atdd:reviewer': [pass, pass], 'atdd:quality-tester': [pass, pass], 'atdd:qa': [qaFail('login', digest), nit] }) })
  assert.equal(done.result.status, 'done')
  assert.deepEqual(done.result.unverified, [digest])
  assert.deepEqual(done.result.advisory.map(f => f.id), ['typo'])
  const stuck = await run(B, { args: 'PROJ-45', agent: make({ 'atdd:preparer': [{ ...prep, maxRounds: 2 }], 'atdd:implementer': [impl('c1'), impl('c2')], 'atdd:reviewer': [pass, pass], 'atdd:quality-tester': [pass, pass], 'atdd:qa': [qaFail('login', digest), qaFail('login', lockout, digest)] }) })
  assert.equal(stuck.result.status, 'needs-human')
  assert.deepEqual(stuck.result.unverified, [digest, lockout])
  const clean = await run(B, { args: 'PROJ-45', agent: make({ 'atdd:preparer': [prep], 'atdd:implementer': [impl('c1')], 'atdd:reviewer': [pass], 'atdd:quality-tester': [pass], 'atdd:qa': [qaPass()] }) })
  assert.deepEqual(clean.result.unverified, [])
  console.log('unverified list ok')
}
// a later QA prompt names the scenarios skipped earlier, which have no replay script, and points to the playbook for them
{
  const digest = skip('Nightly digest is sent', 'idle wait: nightly cron at 02:00 (~11h)')
  const { calls } = await run(B, { args: 'PROJ-45', agent: make({ 'atdd:preparer': [prep], 'atdd:implementer': [impl('c1'), impl('c2')], 'atdd:reviewer': [pass, pass], 'atdd:quality-tester': [pass, pass], 'atdd:qa': [qaFail('login', digest), qaPass(digest)] }) })
  const [first, second] = qaPrompts(calls)
  assert.doesNotMatch(first, /skipped/i)
  assert.match(second, /skipped in an earlier round/)
  assert.match(second, /"Nightly digest is sent" \(idle wait: nightly cron at 02:00 \(~11h\)\)/)
  assert.match(second, /playbook's "Later rounds"/)
  console.log('later QA prompt names earlier skips ok')
}
