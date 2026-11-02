import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// server-only throws outside React Server Components, and tests import server modules directly.
vi.mock('server-only', () => ({}))
afterEach(cleanup)
