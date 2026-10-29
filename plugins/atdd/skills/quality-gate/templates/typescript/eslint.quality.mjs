// atdd quality gate: cognitive complexity for changed TypeScript/JavaScript files.
// Used only by scripts/atdd/gate.py (`eslint -c scripts/atdd/eslint.quality.mjs <files>`), independent
// of the repo's own lint config. The threshold comes from gate.thresholds.cognitive via ATDD_COGNITIVE.
import tseslint from 'typescript-eslint'
import sonarjs from 'eslint-plugin-sonarjs'

const threshold = Number(process.env.ATDD_COGNITIVE ?? 15)

export default [
  {
    files: ['**/*.{ts,tsx,mts,cts,js,jsx,mjs,cjs}'],
    languageOptions: { parser: tseslint.parser, ecmaVersion: 'latest', sourceType: 'module' },
    plugins: { sonarjs },
    linterOptions: { reportUnusedDisableDirectives: 'off', noInlineConfig: true },
    rules: {
      'sonarjs/cognitive-complexity': ['error', threshold],
    },
  },
]
