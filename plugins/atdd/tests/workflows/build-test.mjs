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
