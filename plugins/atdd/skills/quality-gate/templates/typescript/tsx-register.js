// Lets Cucumber.js load TypeScript step definitions (ESM). Referenced first in cucumber.mjs → import.
import { register } from 'tsx/esm/api'

register()
