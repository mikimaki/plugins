import { execFileSync } from 'node:child_process'

const DEFAULT_TEST_DATABASE_URL = 'mysql://app:app@127.0.0.1:3306/__app___test'

export function testDatabaseUrl(): string {
  const url = new URL(process.env.TEST_DATABASE_URL ?? DEFAULT_TEST_DATABASE_URL)
  const strykerWorker = process.env.STRYKER_MUTATOR_WORKER
  if (strykerWorker !== undefined) url.pathname += `_w${strykerWorker}`
  return url.toString()
}

export function resetTestDatabase(url = testDatabaseUrl()): void {
  const name = new URL(url).pathname.slice(1)
  if (!/_test(_w\d+)?$/.test(name))
    throw new Error(`Refusing to reset "${name}": a test database's name ends in _test.`)
  execFileSync('npx', ['prisma', 'migrate', 'reset', '--force'], {
    env: { ...process.env, DATABASE_URL: url },
    stdio: 'inherit',
  })
}
