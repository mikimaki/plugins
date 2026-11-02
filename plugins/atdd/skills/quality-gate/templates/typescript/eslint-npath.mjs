// atdd quality gate: NPath complexity per function, as an ESLint rule (atdd/npath-complexity).
// Loaded by scripts/atdd/eslint.quality.mjs; scripts/atdd/gate.py reads its reports in the npath check.
//
// NPath counts the acyclic execution paths through a function (Nejmeh, 1988), using the table PMD and
// Checkstyle document. A sequence of statements multiplies; a decision adds its alternatives:
//   if            NP(then) + 1 + C(cond)              if/else   NP(then) + NP(else) + C(cond)
//   while, do     NP(body) + C(cond) + 1              for       NP(body) + C(init) + C(test) + C(update) + 1
//   for-in/of     NP(body) + C(iterable) + 1          switch    C(expr) + Σ NP(case) + C(case tests) [+1 without default]
//   try           (NP(try) + NP(catch)) × NP(finally) other     max(1, C(statement))
// C(expression) counts each &&, ||, ??, &&=, ||=, ??= once and each ?: twice. Nested functions are
// measured on their own and add nothing to the function around them.

const LOGICAL_ASSIGNMENTS = new Set(['&&=', '||=', '??='])
const FUNCTIONS = new Set(['FunctionDeclaration', 'FunctionExpression', 'ArrowFunctionExpression'])
const BOUNDARIES = new Set([...FUNCTIONS, 'ClassBody', 'TSEmptyBodyFunctionExpression'])

function childNodes(node, visitorKeys) {
  const nodes = []
  for (const key of visitorKeys[node.type] ?? []) {
    const value = node[key]
    for (const item of Array.isArray(value) ? value : [value]) {
      if (item && typeof item.type === 'string') nodes.push(item)
    }
  }
  return nodes
}

function conditions(node, visitorKeys) {
  if (!node || BOUNDARIES.has(node.type)) return 0n
  let count = 0n
  if (node.type === 'LogicalExpression') count += 1n
  else if (node.type === 'ConditionalExpression') count += 2n
  else if (node.type === 'AssignmentExpression' && LOGICAL_ASSIGNMENTS.has(node.operator)) count += 1n
  for (const child of childNodes(node, visitorKeys)) count += conditions(child, visitorKeys)
  return count
}

function atLeastOne(count) {
  return count > 1n ? count : 1n
}

function sequence(statements, visitorKeys) {
  return statements.reduce((product, statement) => product * paths(statement, visitorKeys), 1n)
}

function switchPaths(node, visitorKeys) {
  const hasDefault = node.cases.some(branch => branch.test === null)
  return node.cases.reduce(
    (total, branch) => total + sequence(branch.consequent, visitorKeys) + conditions(branch.test, visitorKeys),
    conditions(node.discriminant, visitorKeys) + (hasDefault ? 0n : 1n),
  )
}

function paths(node, visitorKeys) {
  switch (node.type) {
    case 'BlockStatement':
    case 'StaticBlock':
      return sequence(node.body, visitorKeys)
    case 'IfStatement':
      return paths(node.consequent, visitorKeys)
        + (node.alternate ? paths(node.alternate, visitorKeys) : 1n)
        + conditions(node.test, visitorKeys)
    case 'SwitchStatement':
      return switchPaths(node, visitorKeys)
    case 'ForStatement':
      return paths(node.body, visitorKeys) + conditions(node.init, visitorKeys)
        + conditions(node.test, visitorKeys) + conditions(node.update, visitorKeys) + 1n
    case 'ForInStatement':
    case 'ForOfStatement':
      return paths(node.body, visitorKeys) + conditions(node.right, visitorKeys) + 1n
    case 'WhileStatement':
    case 'DoWhileStatement':
      return paths(node.body, visitorKeys) + conditions(node.test, visitorKeys) + 1n
    case 'TryStatement':
      return (paths(node.block, visitorKeys) + (node.handler ? paths(node.handler.body, visitorKeys) : 0n))
        * (node.finalizer ? paths(node.finalizer, visitorKeys) : 1n)
    case 'LabeledStatement':
    case 'WithStatement':
      return paths(node.body, visitorKeys)
    case 'FunctionDeclaration':
    case 'ClassDeclaration':
      return 1n
    default:
      return atLeastOne(conditions(node, visitorKeys))
  }
}

export function npathOf(fn, visitorKeys) {
  return fn.body.type === 'BlockStatement'
    ? sequence(fn.body.body, visitorKeys)
    : atLeastOne(conditions(fn.body, visitorKeys))
}

function functionName(fn, sourceCode) {
  if (fn.id) return fn.id.name
  const parent = fn.parent
  if (parent.type === 'VariableDeclarator' || parent.type === 'AssignmentPattern') {
    return sourceCode.getText(parent.id ?? parent.left)
  }
  if (parent.type === 'AssignmentExpression') return sourceCode.getText(parent.left)
  if (['MethodDefinition', 'Property', 'PropertyDefinition', 'TSAbstractMethodDefinition'].includes(parent.type)) {
    return sourceCode.getText(parent.key)
  }
  return 'anonymous function'
}

const npathComplexity = {
  meta: {
    type: 'suggestion',
    docs: { description: 'Limit the NPath complexity (acyclic execution paths) of each function' },
    schema: [{
      type: 'object',
      properties: { max: { type: 'integer', minimum: 1 } },
      additionalProperties: false,
    }],
    messages: {
      tooComplex: "'{{name}}' has an NPath complexity of {{npath}}; the maximum is {{max}}.",
    },
  },
  create(context) {
    const max = BigInt(context.options[0]?.max ?? 200)
    const { sourceCode } = context
    const check = fn => {
      const npath = npathOf(fn, sourceCode.visitorKeys)
      if (npath > max) {
        context.report({
          node: fn,
          messageId: 'tooComplex',
          data: { name: functionName(fn, sourceCode), npath: String(npath), max: String(max) },
        })
      }
    }
    return { FunctionDeclaration: check, FunctionExpression: check, ArrowFunctionExpression: check }
  },
}

const plugin = {
  meta: { name: 'atdd-npath' },
  rules: { 'npath-complexity': npathComplexity },
}

export default plugin
