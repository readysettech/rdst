import { cleanup, render, screen } from '@testing-library/react'
import type { ComponentType } from 'react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', async () => {
  const { fileRouteModuleMock } = await import('@/test-utils')
  return fileRouteModuleMock({ useNavigate: () => vi.fn() })
})

const scan = {
  startScan: vi.fn(),
  cancel: vi.fn(),
  reset: vi.fn(),
  state: 'idle' as string,
  phase: null,
  phaseProgress: null,
  statusMessage: null,
  files: [],
  queries: [],
  summary: null,
  error: null as string | null,
}

vi.mock('../lib/useScan', () => ({ useScan: () => scan }))
vi.mock('../hooks/useTarget', () => ({
  useTargetResolution: () => ({
    target: 'orders',
    isResolving: false,
    isUnavailable: false,
    refetch: vi.fn(),
  }),
  useTarget: () => ({ target: 'orders' }),
}))
vi.mock('../lib/useTargetPasswordLock', () => ({
  useTargetPasswordLock: () => ({
    isLocked: false,
    isResolved: true,
    message: null,
    missingTargetRequirements: [],
    keyringAvailable: true,
  }),
}))
vi.mock('../lib/useCacheAction', () => ({
  useCacheAction: () => ({ cacheQuery: vi.fn(), cachingId: null }),
}))
vi.mock('../hooks/useRecentScanDirs', () => ({
  useRecentScanDirs: () => ({ recentDirs: [], addRecentDir: vi.fn() }),
}))
// The directory field browses the server; this suite is about the page, not
// the picker, and a real query here would need a client.
vi.mock('../lib/useBrowse', () => ({
  useBrowse: () => ({ data: undefined, isLoading: false, isError: false }),
}))

import { Route } from './scan'

let ScanPage: ComponentType

beforeAll(async () => {
  const split = await (
    Route as unknown as {
      component: () => Promise<{ component: ComponentType }>
    }
  ).component()
  ScanPage = split.component
})

afterEach(() => {
  cleanup()
  scan.state = 'idle'
  scan.error = null
})

describe('scan identity', () => {
  it('carries the page hero and says why it is off the nav', () => {
    render(<ScanPage />)

    expect(screen.getByRole('heading', { name: 'Code scan' })).toBeTruthy()
    expect(screen.getByText(/Scan your codebase for ORM queries/i)).toBeTruthy()
    expect(screen.getByText(/kept out of the sidebar/i)).toBeTruthy()
  })
})

describe('scan failure', () => {
  it('offers to re-run the same scan instead of a bare red line', () => {
    scan.state = 'error'
    scan.error = '500: Scanner crashed on orders.ts'
    render(<ScanPage />)

    // Was: a hand-rolled div reading "Error: 500: …" with no retry. [E-43]
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(screen.getByText('The scan stopped')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Technical details' })
    ).toBeTruthy()
  })
})
