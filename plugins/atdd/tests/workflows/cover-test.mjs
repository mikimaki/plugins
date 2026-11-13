import { run } from './run.mjs'
import { readFileSync } from 'node:fs'
import assert from 'node:assert/strict'
const C = process.argv[2]
const prep = { ok: true, key: 'a01', title: 'Goals', specPath: '.atdd/a01/spec.md', featureDir: 'features/a01/', featureFiles: ['features/a01/goal-range.feature'], scenarioCount: 2, branch: 'atdd/cover-a01-goal-range', parent: 'atdd/cover-a01-accepted-goals', base: 'b1', baseNote: '', scenariosCommit: 's1', testPaths: ['tests/**', 'features/**'], unfinishedWork: [], workDir: '.atdd/a01/cover/goal-range', maxRounds: 2 }
const unitArgs = { issue: 'a01', unit: { source: 'inventory', feature: 'features/a01/goal-range.feature', branch: 'atdd/cover-a01-goal-range', stackTop: 'atdd/cover-a01-accepted-goals' } }
const cover = (over = {}) => ({ sha: 'c1', summary: '', targets: ['src/goals.py'], testsRun: [], responses: [], discrepancies: [], blocked: false, ...over })
const pass = { verdict: 'pass', findings: [], summary: 'ok', results: [{ name: 'acceptance-coverage', status: 'warn', detail: '95%' }] }
function agentFor(script) { return async (prompt, opts) => { const t = opts.agentType; const next = script[t].shift(); if (typeof next === 'function') return next(prompt); return next } }
// done in unit mode
{
  const prompts = []
  const { result } = await run(C, { args: unitArgs, agent: async (p, o) => { prompts.push([o.agentType, p]); return agentFor({ 'atdd:preparer': [prep], 'atdd:acceptance-test-coverer': [cover()], 'atdd:coverage-reviewer': [pass], 'atdd:coverage-quality-tester': [pass] })(p, o) } })
  assert.equal(result.status, 'done'); assert.equal(result.agentCalls, 4); assert.equal(result.unit, 'features/a01/goal-range.feature'); assert.equal(result.parent, 'atdd/cover-a01-accepted-goals')
  assert.equal(result.metrics[0].detail, '95%'); assert.equal(result.scope, undefined)
  const prepPrompt = prompts[0][1]; assert.match(prepPrompt, /stack top.*atdd\/cover-a01-accepted-goals/)
  const coverPrompt = prompts[1][1]; assert.match(coverPrompt, /goal-range\.feature only/); assert.match(coverPrompt, /\.atdd\/a01\/cover\/goal-range\/targets\.txt/)
  const qualityPrompt = prompts[3][1]; assert.match(qualityPrompt, /ATDD_OUT=\.atdd\/a01\/cover\/goal-range\/quality\/round-1/)
  console.log('done ok')
}
// state of the agents is per call; build fresh scripts per scenario
const make = s => { const sc = JSON.parse(JSON.stringify(s)); return async (p, o) => sc[o.agentType].shift() }
// discrepancies -> unit scope
{
  const { result } = await run(C, { args: unitArgs, agent: make({ 'atdd:preparer': [prep], 'atdd:acceptance-test-coverer': [cover({ sha: '', discrepancies: [{ scenario: 's', step: 'Then', expected: 'a', actual: 'b' }] })] }) })
  assert.equal(result.status, 'discrepancies'); assert.equal(result.scope, 'unit'); assert.match(result.next, /cover-queue inventory features\/a01\/goal-range.feature --resume-parked/)
  console.log('discrepancies ok')
}
// repo-scope human finding from quality
{
  const fail = { verdict: 'fail', findings: [{ id: 'q1', severity: 'blocker', audience: 'human', scope: 'repo', title: 'mutmut missing', detail: '' }], summary: '' }
  const { result } = await run(C, { args: unitArgs, agent: make({ 'atdd:preparer': [prep], 'atdd:acceptance-test-coverer': [cover()], 'atdd:coverage-reviewer': [pass], 'atdd:coverage-quality-tester': [fail] }) })
  assert.equal(result.status, 'needs-human'); assert.equal(result.scope, 'repo')
  console.log('repo scope ok')
}
// blocked with unit scope; prep failure with unit scope; out of rounds
{
  let r = await run(C, { args: unitArgs, agent: make({ 'atdd:preparer': [prep], 'atdd:acceptance-test-coverer': [cover({ sha: '', blocked: true, blockedReason: 'clock', blockedScope: 'unit' })] }) })
  assert.equal(r.result.scope, 'unit')
  r = await run(C, { args: unitArgs, agent: make({ 'atdd:preparer': [{ ok: false, reason: 'gherkin errors', failureScope: 'unit', branch: 'atdd/cover-a01-goal-range' }] }) })
  assert.equal(r.result.scope, 'unit'); assert.equal(r.result.branch, 'atdd/cover-a01-goal-range')
  const major = { verdict: 'fail', findings: [{ id: 'r1', severity: 'major', title: 'weak', detail: '' }], summary: '' }
  r = await run(C, { args: unitArgs, agent: make({ 'atdd:preparer': [prep], 'atdd:acceptance-test-coverer': [cover(), cover({ sha: 'c2' })], 'atdd:coverage-reviewer': [major, major] }) })
  assert.equal(r.result.status, 'needs-human'); assert.equal(r.result.scope, 'unit'); assert.match(r.result.reason, /Still failing after 2/)
  console.log('unit scopes ok')
}
// plain /atdd:cover still works as before
{
  const plain = { ...prep, featureFiles: undefined, workDir: undefined, parent: undefined }
  const prompts = []
  const { result } = await run(C, { args: 'PROJ-45', agent: async (p, o) => { prompts.push(p); return make({ 'atdd:preparer': [plain], 'atdd:acceptance-test-coverer': [cover()], 'atdd:coverage-reviewer': [pass], 'atdd:coverage-quality-tester': [pass] })(p, o) } })
  assert.equal(result.status, 'done'); assert.equal(result.unit, undefined); assert.equal(prompts[0], 'Mode: cover. Issue reference: PROJ-45')
  assert.match(prompts[1], /\.atdd\/a01\/cover\/targets\.txt/); assert.match(result.next, /push the branch and open a PR/)
  console.log('plain ok')
}
// no QA stage: coverage reviewer, then the cover gate
{
  const { result, calls, logs } = await run(C, { args: 'PROJ-45', agent: make({ 'atdd:preparer': [prep], 'atdd:acceptance-test-coverer': [cover()], 'atdd:coverage-reviewer': [pass], 'atdd:coverage-quality-tester': [pass] }) })
  assert.equal(result.status, 'done')
  assert.deepEqual(calls.map(c => c.opts.agentType), ['atdd:preparer', 'atdd:acceptance-test-coverer', 'atdd:coverage-reviewer', 'atdd:coverage-quality-tester'])
  assert.deepEqual(result.history.map(h => h.stage), ['review', 'quality'])
  assert.ok(!logs.some(l => /QA/.test(l)), logs.join('\n'))
  const meta = new Function(readFileSync(C, 'utf8').match(/export const meta = (\{[\s\S]*?\n\})/)[1].replace(/^/, 'return '))()
  assert.deepEqual(meta.phases.map(ph => ph.title), ['Prepare', 'Cover', 'Review', 'Quality'])
  assert.doesNotMatch(meta.description, /QA/)
  console.log('no qa ok')
}
