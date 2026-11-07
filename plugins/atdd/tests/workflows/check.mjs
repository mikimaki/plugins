// Syntax-check a workflow script: the body runs as an async function with the workflow globals as parameters.
import { readFileSync } from 'node:fs'
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
for (const file of process.argv.slice(2)) {
  const body = readFileSync(file, 'utf8').replace(/^export const meta/m, 'const meta')
  try { new AsyncFunction('args', 'agent', 'parallel', 'pipeline', 'phase', 'log', 'workflow', 'budget', body); console.log('ok', file) }
  catch (e) { console.log('SYNTAX', file, e.message); process.exitCode = 1 }
}
