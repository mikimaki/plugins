import { run } from './run.mjs'
import assert from 'node:assert/strict'
const Q = process.argv[2]
const units = (statuses = {}) => ['a01/accepted-goals', 'a01/goal-range', 'a02/below-the-goal', 'a02/reaching-the-goal'].map(u => {
  const [key, stem] = u.split('/')
  const branch = `atdd/cover-${key}-${stem}`
  const s = statuses[u] || {}
  return { key, file: `${stem}.feature`, feature: `features/${u}.feature`, branch, status: s.status || 'pending', merged: !!s.merged, parent: s.parent || '', pr: s.pr || '', reason: s.reason || '', scenarios: 2, checkErrors: s.checkErrors || [] }
})
function mockAgent(plan, published, parked) {
  return async (prompt, opts) => {
    const mode = /Mode: ([\w-]+)/.exec(prompt)[1]
    if (mode === 'cover-queue-plan') return plan
    if (mode === 'cover-publish') { const b = /Unit branch: (.*)/.exec(prompt)[1]; const base = /Stack parent \(the PR base\): (.*)/.exec(prompt)[1]; published.push({ b, base, body: prompt }); return { ok: true, recorded: true, pushed: [b], prUrl: `https://pr/${published.length}` } }
    if (mode === 'cover-park') { parked.push(/Unit branch: (.*)/.exec(prompt)[1]); return { ok: true, parkCommit: 'abc', committedFiles: ['tests/x.py'], leftovers: [] } }
    if (mode === 'cover-queue-summary') return { areas: [{ key: 'a01', complete: true, units: 2, done: 1, parked: 1, uncoveredRequirements: [] }] }
    throw new Error('unexpected ' + mode)
  }
}
const basePlan = (us, extra = {}) => ({ ok: true, problems: [], notes: [], integrationBranch: 'atdd/cover-base', remote: 'origin', openPrs: true, top: 'atdd/cover-base', maxConsecutiveParks: 3, pluginRoot: '/plugin', units: us, ...extra })
const done = (u, stackTop) => ({ status: 'done', sha: 'sha-' + u.branch, rounds: 1, history: [{ stage: 'review' }, { stage: 'quality' }], parent: stackTop, agentCalls: 4, scenarioCount: 2, metrics: [{ name: 'acceptance-coverage', status: 'warn', detail: '90% of target lines' }], advisory: [{ title: 'gap', audience: 'human', stage: 'quality' }], workDir: '.atdd/x/cover/y', targets: ['src/a.py'] })

// A: first run — goal-range has discrepancies
{
  const published = [], parked = [], cover = []
  const { result } = await run(Q, {
    args: 'inventory a01 a02 --open-prs',
    agent: mockAgent(basePlan(units()), published, parked),
    workflow: async (name, a) => { cover.push({ name, a }); return a.unit.feature.includes('goal-range') ? { status: 'discrepancies', discrepancies: [{ scenario: '50,000', step: 'Then', expected: 'saved', actual: 'refused' }], agentCalls: 2, parent: a.unit.stackTop, scope: 'unit' } : done(a.unit, a.unit.stackTop) },
  })
  assert.deepEqual(cover.map(c => c.a.unit.stackTop), ['atdd/cover-base', 'atdd/cover-a01-accepted-goals', 'atdd/cover-a01-accepted-goals', 'atdd/cover-a02-below-the-goal'])
  assert.deepEqual(published.map(p => p.base), ['atdd/cover-base', 'atdd/cover-a01-accepted-goals', 'atdd/cover-a02-below-the-goal'])
  assert.deepEqual(parked, ['atdd/cover-a01-goal-range'])
  assert.equal(result.status, 'finished'); assert.equal(result.counts.done, 3); assert.equal(result.counts.parked, 1)
  assert.match(published[0].body, /90% of target lines/); assert.match(published[0].body, /- gap/)
  console.log('A ok', JSON.stringify(result.units.map(u => [u.unit, u.status, u.parent, u.pr])))
}
// B: second run — all done or parked, nothing runs
{
  const published = [], parked = [], cover = []
  const st = { 'a01/accepted-goals': { status: 'done', pr: 'u1', parent: 'atdd/cover-base' }, 'a01/goal-range': { status: 'parked', reason: 'discrepancies' }, 'a02/below-the-goal': { status: 'done', pr: 'u3' }, 'a02/reaching-the-goal': { status: 'done', pr: 'u4' } }
  const { result } = await run(Q, { args: 'inventory a01 a02 --open-prs', agent: mockAgent(basePlan(units(st), { top: 'atdd/cover-a02-reaching-the-goal' }), published, parked), workflow: async (n, a) => { cover.push(a); return done(a.unit) } })
  assert.equal(cover.length, 0); assert.equal(published.length, 0); assert.equal(parked.length, 0); assert.equal(result.ran, 0)
  console.log('B ok', JSON.stringify(result.units.map(u => [u.unit, u.status])))
}
// C: resume a parked unit explicitly; stack top passed through
{
  const published = [], parked = [], cover = []
  const st = { 'a01/accepted-goals': { status: 'done', pr: 'u1' }, 'a01/goal-range': { status: 'parked', reason: 'd' }, 'a02/below-the-goal': { status: 'done', pr: 'u3' }, 'a02/reaching-the-goal': { status: 'done', pr: 'u4' } }
  const { result } = await run(Q, { args: 'inventory a01/goal-range.feature --resume-parked', agent: mockAgent(basePlan(units(st), { top: 'atdd/cover-a02-reaching-the-goal' }), published, parked), workflow: async (n, a) => { cover.push(a); return done(a.unit, a.unit.stackTop) } })
  assert.equal(cover.length, 1); assert.equal(cover[0].unit.stackTop, 'atdd/cover-a02-reaching-the-goal'); assert.equal(published[0].base, 'atdd/cover-a02-reaching-the-goal')
  console.log('C ok')
}
// D: repo-scope failure stops the queue after parking
{
  const published = [], parked = [], cover = []
  const { result } = await run(Q, { args: 'inventory a01 a02', agent: mockAgent(basePlan(units(), { openPrs: false }), published, parked), workflow: async (n, a) => { cover.push(a); return { status: 'needs-human', stage: 'quality', scope: 'repo', reason: 'mutmut is not installed', agentCalls: 3 } } })
  assert.equal(cover.length, 1); assert.equal(result.status, 'stopped'); assert.equal(result.units.filter(u => u.status === 'not run').length, 3)
  console.log('D ok', result.stopReason)
}
// E: consecutive unit parks stop at maxConsecutiveParks; name lookup failure falls back to scriptPath
{
  const published = [], parked = [], names = []
  const plan = basePlan(units(), { maxConsecutiveParks: 2, openPrs: false })
  const { result, logs } = await run(Q, { args: 'inventory a01 a02', agent: mockAgent(plan, published, parked), workflow: async (n, a) => { names.push(n); if (n === 'atdd:cover') throw new Error('unknown workflow'); return { status: 'needs-human', scope: 'unit', stage: 'cover', reason: 'missing seam', agentCalls: 2 } } })
  assert.equal(parked.length, 2); assert.equal(result.status, 'stopped'); assert.equal(names[1].scriptPath, '/plugin/workflows/cover.js')
  console.log('E ok', result.stopReason)
}
// F: --max-units and invalid scenarios
{
  const published = [], parked = [], cover = []
  const st = { 'a01/accepted-goals': { checkErrors: ['x.feature:3 [no-then] ...'] } }
  const { result } = await run(Q, { args: 'inventory a01 a02 --max-units 2', agent: mockAgent(basePlan(units(st), { openPrs: false }), published, parked), workflow: async (n, a) => { cover.push(a); return done(a.unit, a.unit.stackTop) } })
  assert.equal(cover.length, 2); assert.equal(result.status, 'partial'); assert.equal(result.units[0].status, 'invalid scenarios')
  console.log('F ok', JSON.stringify(result.units.map(u => [u.unit, u.status])))
}
// G: plan problems stop before anything runs; bad args
{
  const { result } = await run(Q, { args: 'inventory a01', agent: async () => ({ ok: false, problems: ['dirty tree'], units: [] }), workflow: async () => { throw new Error('no') } })
  assert.equal(result.status, 'stopped'); assert.deepEqual(result.problems, ['dirty tree'])
  const r2 = await run(Q, { args: 'inventory', agent: async () => ({}), workflow: async () => ({}) })
  assert.equal(r2.result.status, 'needs-input')
  console.log('G ok')
}
