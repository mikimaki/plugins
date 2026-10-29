/**
 * Starter architecture rules for the atdd gate (copied to .dependency-cruiser.cjs by /atdd:init).
 * The generic rules below are safe everywhere. Replace the commented example with the repo's real
 * layering (see docs/architecture.md and the ADRs) — the gate only enforces what's written here.
 */
module.exports = {
  forbidden: [
    { name: 'no-circular', severity: 'error', from: {}, to: { circular: true } },
    { name: 'not-to-unresolvable', severity: 'error', from: {}, to: { couldNotResolve: true } },
    {
      name: 'not-to-dev-dep',
      severity: 'error',
      comment: 'Production code must not import devDependencies.',
      from: { path: '^src', pathNot: '\\.(spec|test)\\.[cm]?[jt]sx?$' },
      to: { dependencyTypes: ['npm-dev'] },
    },
    // {
    //   name: 'domain-is-pure',
    //   severity: 'error',
    //   from: { path: '^src/domain' },
    //   to: { path: '^src/(infrastructure|api|ui)' },
    // },
  ],
  options: {
    doNotFollow: { path: 'node_modules' },
    tsPreCompilationDeps: true,
    tsConfig: { fileName: 'tsconfig.json' },
  },
}
