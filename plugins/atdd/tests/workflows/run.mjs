// Runs a workflow script with mocked agent()/workflow() to exercise its control flow.
import { readFileSync } from 'node:fs'
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
export function load(file) {
  const body = readFileSync(file, 'utf8').replace(/^export const meta/m, 'const meta')
  return new AsyncFunction('args', 'agent', 'parallel', 'pipeline', 'phase', 'log', 'workflow', 'budget', body)
}
export async function run(file, { args, agent, workflow }) {
  const logs = [], calls = []
  const wrappedAgent = async (prompt, opts) => { calls.push({ prompt, opts }); return agent(prompt, opts) }
  const result = await load(file)(args, wrappedAgent, null, null, () => {}, m => logs.push(m), workflow, {})
  return { result, logs, calls }
}
