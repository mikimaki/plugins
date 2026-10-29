// Cucumber.js config for the approved scenarios. strict: undefined, ambiguous and pending steps fail.
export default {
  paths: ['features/**/*.feature'],
  import: ['./scripts/atdd/tsx-register.js', 'features/steps/**/*.ts', 'features/support/**/*.ts'],
  format: ['progress', ['message', 'reports/cucumber.ndjson']],
  strict: true,
}
