/**
 * Architecture rules for the atdd gate (copied to .dependency-cruiser.cjs by /atdd:init). The rules below hold in
 * any Next.js app; replace the commented example with this repo's real layering (docs/architecture.md and the
 * ADRs), because the gate enforces only what is written here. Cycles between folders and between workspace
 * packages are the gate's dependency-cycles check, whatever this file says.
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
    {
      name: 'routes-are-the-composition-root',
      severity: 'error',
      comment: 'src/app holds routes. They compose features; nothing outside src/app imports from it.',
      from: { pathNot: '^src/app/' },
      to: { path: '^src/app/' },
    },
    // {
    //   name: 'database-only-in-server-modules',
    //   severity: 'error',
    //   comment: 'UI code reaches data through server actions and queries, never through the Prisma client.',
    //   from: { path: '^src/(components|features/[^/]+/components)/' },
    //   to: { path: '^src/(lib/db\\.ts|generated/prisma/)' },
    // },
  ],
  options: {
    doNotFollow: { path: 'node_modules' },
    exclude: { path: '^src/generated/' },
    tsPreCompilationDeps: true,
    tsConfig: { fileName: 'tsconfig.json' },
  },
}
