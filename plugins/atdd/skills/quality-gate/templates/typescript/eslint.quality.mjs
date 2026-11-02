// atdd quality gate: cognitive and NPath complexity of changed TypeScript/JavaScript functions.
// Used only by scripts/atdd/gate.py (`eslint -c scripts/atdd/eslint.quality.mjs <files>`), independent
// of the repo's own lint config. The thresholds come from gate.thresholds (cognitive, npath) through
// ATDD_COGNITIVE and ATDD_NPATH.
import tseslint from 'typescript-eslint'
import sonarjs from 'eslint-plugin-sonarjs'
import atdd from './eslint-npath.mjs'

const cognitive = Number(process.env.ATDD_COGNITIVE ?? 15)
const npath = Number(process.env.ATDD_NPATH ?? 200)

const config = [
  {
    files: ['**/*.{ts,tsx,mts,cts,js,jsx,mjs,cjs}'],
    languageOptions: {
      parser: tseslint.parser,
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { sonarjs, atdd },
    linterOptions: { reportUnusedDisableDirectives: 'off', noInlineConfig: true },
    rules: {
      'sonarjs/cognitive-complexity': ['error', cognitive],
      'atdd/npath-complexity': ['error', { max: npath }],
    },
  },
]

export default config
